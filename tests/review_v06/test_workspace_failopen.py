"""Attacks on v0.6 workspace coverage (workspace.py; inventory `Gate.covered_by`; decisions
`_apply_coverage`; architecture §3.5).

The attacker edits the working tree only (BASE is the honest repository). The goal: change the
code of workspace package `packages/a` while its own tests do not run, and still get a plan whose
required gates can all PASS. A covered package gate is SKIPPED, so the only thing that can vouch
for the package is the root gate; that is safe only when the root run really reaches the package,
or when the root gate cannot PASS (M14 definition change, or a v0.2 taint).

Each scenario is planned with `api.plan` on a real git repository with stub tools, as in
tests/field/test_field_findings.py. `vouched()` is the secure property.

What these tests rely on about external tools (certainty noted per test):
- pnpm reads its workspace ONLY from pnpm-workspace.yaml; package.json "workspaces" is ignored
  by pnpm (certain: pnpm documentation, "pnpm-workspace.yaml ... defines the root of the
  workspace"; npm/yarn "workspaces" is not read by pnpm).
- vitest < 4 evaluates vitest.workspace.* as a JS module and uses its default export (certain);
  a directory pattern with no config is a project (vitest docs).
- jest's `testFailureExitCode` is a global config option honoured in multi-project runs
  (documented config option; high confidence).
- lerna >= 6 runs `lerna run` through the Nx task runner, which merges package.json scripts with
  project.json targets, project.json winning (likely, not verified offline).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from repomarshal import api
from repomarshal.model import DecisionStatus, Plan
from tests.helpers import GitRepo, env_with_path, stub_bin

failopen = pytest.mark.failopen

STUB_TOOLS = ("npm", "pnpm", "yarn", "npx", "make", "python", "ruff")
PKG = "packages/a"
PKG_GATE = "packages/a:unit"
ROOT_GATE = "root:unit"


def pj(scripts: dict, **extra) -> str:
    return json.dumps({"name": extra.pop("name", "x"), "scripts": scripts, **extra}, indent=2)


def package_files(script: str = "vitest run") -> dict[str, str]:
    files: dict[str, str] = {}
    for name in ("a", "b"):
        files[f"packages/{name}/package.json"] = pj({"test": script}, name=name)
        files[f"packages/{name}/src/index.ts"] = f"export const {name} = 1;\n"
        files[f"packages/{name}/src/index.test.ts"] = (
            f'import {{ {name} }} from "./index";\n'
            f'test("{name}", () => {{ expect({name}).toBe(1); }});\n'
        )
    return files


@dataclass
class Scenario:
    repo: GitRepo
    base: str
    env: dict[str, str]

    def plan(self) -> Plan:
        return api.plan(self.repo.root, base=self.base, env=self.env)


def scenario(tmp_path: Path, base: dict[str, str], work: dict[str, str]) -> Scenario:
    repo = GitRepo(tmp_path / "mono")
    for path, text in base.items():
        repo.write(path, text)
    sha = repo.commit("BASE")
    for path, text in work.items():
        repo.write(path, text)
    bindir = tmp_path / "bin"
    for tool in STUB_TOOLS:
        stub_bin(bindir, tool)
    return Scenario(repo, sha, env_with_path(bindir, keep_system=False))


def explain(plan: Plan) -> str:
    rows = [f"{d.gate_id or d.kind}: {d.status} taint={len(d.taint)}" for d in plan.gates]
    rows += [f"  {r.text}" for d in plan.gates if d.gate_id == PKG_GATE for r in d.reasons]
    rows += [f"signal {s.id}: {', '.join(s.files)}" for s in plan.signals]
    rows += [f"gate {g.id}: covered_by={g.covered_by} changed={g.definition_changed}"
             for g in plan.inventory if g.kind.value == "unit"]  # fmt: skip
    return "\n".join(rows)


def vouched(plan: Plan) -> bool:
    """The secure property: the package's tests run (its gate is REQUIRED), or the root gate that
    covers it cannot report PASS (definition changed, or tainted)."""
    by_id = {d.gate_id: d for d in plan.gates if d.gate_id}
    gates = {g.id: g for g in plan.inventory}
    pkg = by_id[PKG_GATE]
    if pkg.status is DecisionStatus.REQUIRED:
        return True
    root = by_id[ROOT_GATE]
    return root.status is not DecisionStatus.REQUIRED or bool(
        root.taint or gates[ROOT_GATE].definition_changed
    )


def covered(plan: Plan) -> bool:
    by_id = {d.gate_id: d for d in plan.gates if d.gate_id}
    return by_id[PKG_GATE].status is DecisionStatus.SKIPPED and any(
        "covered by" in r.text for r in by_id[PKG_GATE].reasons
    )


CODE_EDIT = {f"{PKG}/src/index.ts": "export const a = 2; // the change under review\n"}


# --------------------------------------------------------------------------- pnpm workspace


PNPM_ROOT = {
    "package.json": pj({"test": "pnpm -r test"}, packageManager="pnpm@9.1.0"),
    "pnpm-workspace.yaml": "packages:\n  - packages/*\n",
    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
}


@failopen
def test_pnpm_workspace_yaml_drops_package_while_package_json_workspaces_lists_it(tmp_path):
    """pnpm reads only pnpm-workspace.yaml, but inventory `_workspace_globs` (inventory.py:735)
    takes the UNION with package.json "workspaces". The change removes packages/a from
    pnpm-workspace.yaml and adds a `workspaces` field (ignored by pnpm, and not part of any gate
    definition) to the root package.json: `pnpm -r test` no longer runs packages/a, yet the
    worktree check `_in_workspace` still says it is a workspace package, so packages/a:unit is
    SKIPPED as covered."""
    s = scenario(
        tmp_path,
        {**PNPM_ROOT, **package_files()},
        {
            **CODE_EDIT,
            "pnpm-workspace.yaml": "packages:\n  - packages/b\n",
            "package.json": pj(
                {"test": "pnpm -r test"}, packageManager="pnpm@9.1.0", workspaces=["packages/*"]
            ),
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


@failopen
def test_pnpm_workspace_yaml_block_scalar_hides_an_item_from_pnpm(tmp_path):
    """`_pnpm_workspace_packages` (inventory.py:758) reads YAML line by line. A literal block
    scalar item `- |-` followed by an indented `- packages/a` line is, for YAML (and pnpm), the
    one string "- packages/a", a glob that matches nothing; the line parser reads it as the item
    `packages/a`. pnpm no longer runs packages/a, coverage still holds in the worktree."""
    s = scenario(
        tmp_path,
        {**PNPM_ROOT, **package_files()},
        {
            **CODE_EDIT,
            "pnpm-workspace.yaml": "packages:\n  - packages/b\n  - |-\n    - packages/a\n",
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


def test_pnpm_workspace_negation_in_worktree_is_defended(tmp_path):
    """Defended: `!packages/a` added to pnpm-workspace.yaml makes the worktree coverage check
    fail, so packages/a:unit stays REQUIRED."""
    s = scenario(
        tmp_path,
        {**PNPM_ROOT, **package_files()},
        {**CODE_EDIT, "pnpm-workspace.yaml": "packages:\n  - packages/*\n  - '!packages/a'\n"},
    )
    plan = s.plan()
    assert vouched(plan) and not covered(plan), explain(plan)


def test_baseline_pnpm_recursive_coverage_applies(tmp_path):
    """Control (defended baseline): with no trick, packages/a:unit is covered by root:unit (an
    untainted, unchanged root gate that really runs the package), so the attacks above exercise
    the coverage path and nothing else."""
    s = scenario(tmp_path, {**PNPM_ROOT, **package_files()}, CODE_EDIT)
    plan = s.plan()
    assert covered(plan), explain(plan)


# --------------------------------------------------------------------------- vitest workspace


VITEST_WS_ROOT = {
    "package.json": pj({"test": "vitest run"}, workspaces=["packages/*"]),
    "package-lock.json": "{}\n",
    "vitest.workspace.ts": 'export default ["packages/*"];\n',
}


@failopen
def test_vitest_workspace_file_evaluated_array_differs_from_its_literals(tmp_path):
    """`_js_default_array` (workspace.py:394, via `_array_after` :339) collects the string
    literals of the array after `export default` and ignores what follows the closing bracket.
    The worktree turns
    vitest.workspace.ts into `["packages/b", "packages/*"].slice(0, 1)`, which vitest evaluates
    to ["packages/b"]. vitest.workspace.* is neither a definition file of the root gate
    (inventory SCRIPT_CONFIG_PREFIXES, inventory.py:209) nor fires tests.discovery_narrowed (no
    JS_CONFIG_KEYS key in the changed line), so root:unit is untainted and can PASS while
    packages/a's tests never run."""
    s = scenario(
        tmp_path,
        {**VITEST_WS_ROOT, **package_files()},
        {
            **CODE_EDIT,
            "vitest.workspace.ts": 'export default ["packages/b", "packages/*"].slice(0, 1);\n',
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


@failopen
def test_vitest_workspace_regex_literal_hides_the_real_array(tmp_path):
    """Same root cause, through the tokenizer: `_js_scan` (workspace.py:299) has no regex
    literals, so `/'/` opens a "string" for the scanner. The module really exports
    ["packages/b"]; the scanner sees `export default ['packages/*']` inside the trailing comment."""
    text = "const re = /'/;\nexport default ['packages/b'];\n//' ; export default ['packages/*']\n"
    s = scenario(
        tmp_path,
        {**VITEST_WS_ROOT, **package_files()},
        {**CODE_EDIT, "vitest.workspace.ts": text},
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


VITEST_CONFIG_ROOT = {
    "package.json": pj({"test": "vitest run"}, workspaces=["packages/*"]),
    "package-lock.json": "{}\n",
    "vitest.config.ts": 'export default { test: { projects: ["packages/*"] } };\n',
}


def test_root_vitest_config_edit_is_defended(tmp_path):
    """Defended: editing the root vitest.config.ts (here `.slice` on projects) is a definition
    change of root:unit (M14), so root:unit cannot PASS."""
    s = scenario(
        tmp_path,
        {**VITEST_CONFIG_ROOT, **package_files()},
        {
            **CODE_EDIT,
            "vitest.config.ts": (
                'export default { test: { projects: ["packages/b", "packages/*"].slice(0, 1) } };\n'
            ),
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


def test_package_vitest_config_edit_is_defended(tmp_path):
    """Defended: a vitest.config.ts added in packages/a (which the root project run would read,
    e.g. to set `test.include: []`) is a definition change of packages/a:unit, which then stays
    REQUIRED (and of root:unit too)."""
    s = scenario(
        tmp_path,
        {**VITEST_CONFIG_ROOT, **package_files()},
        {
            **CODE_EDIT,
            f"{PKG}/vitest.config.ts": 'export default { test: { include: ["none/**"] } };\n',
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


def test_focused_test_in_package_is_defended(tmp_path):
    """Defended: `test.only` added in a covered package's test file fires tests.skip_added,
    which taints root:unit (the root scope covers every file)."""
    s = scenario(
        tmp_path,
        {**VITEST_CONFIG_ROOT, **package_files()},
        {
            **CODE_EDIT,
            f"{PKG}/src/index.test.ts": (
                'import { a } from "./index";\ntest.only("a", () => { expect(a).toBe(2); });\n'
            ),
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


# --------------------------------------------------------------------------- jest projects


JEST_ROOT = {
    "package.json": pj(
        {"test": "jest"},
        workspaces=["packages/*"],
        jest={"projects": ["<rootDir>/packages/*"]},
    ),
    "package-lock.json": "{}\n",
}


@failopen
def test_root_package_json_jest_global_option_hides_package_failures(tmp_path):
    """The root jest config in package.json "jest" is neither part of root:unit's definition
    (`_script_definition` hashes only the script, hooks and pm; package.json is a SEMANTIC file,
    inventory.py:221) nor checked by tests.discovery_narrowed beyond its selection keys. Adding
    the global option `"testFailureExitCode": 0` makes the root multi-project run exit 0 on
    failing tests. Before v0.6 packages/a:unit ran `jest` in packages/a (its own package.json is
    the config there) and failed; now it is SKIPPED as covered."""
    s = scenario(
        tmp_path,
        {**JEST_ROOT, **package_files("jest")},
        {
            **CODE_EDIT,
            "package.json": pj(
                {"test": "jest"},
                workspaces=["packages/*"],
                jest={"projects": ["<rootDir>/packages/*"], "testFailureExitCode": 0},
            ),
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


def test_root_jest_projects_narrowed_is_defended(tmp_path):
    """Defended: narrowing the root jest `projects` to packages/b fails the worktree coverage
    check (and fires tests.discovery_narrowed)."""
    s = scenario(
        tmp_path,
        {**JEST_ROOT, **package_files("jest")},
        {
            **CODE_EDIT,
            "package.json": pj(
                {"test": "jest"},
                workspaces=["packages/*"],
                jest={"projects": ["<rootDir>/packages/b"]},
            ),
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)


# --------------------------------------------------------------------------- lerna / nx


@failopen
def test_lerna_run_with_project_json_target_override(tmp_path):
    """`lerna run test` is accepted as evidence (workspace.py:185), but lerna >= 6 runs through
    the Nx task runner, where a packages/a/project.json target `test` replaces the package.json
    script (the very reason workspace.py rejects `nx run-many`). project.json is not a definition
    file of any gate (inventory `_is_definition_file`), so adding one that runs `true` keeps
    coverage and root:unit can PASS. Certainty about lerna/Nx behaviour: likely, not verified
    offline."""
    s = scenario(
        tmp_path,
        {
            "package.json": pj({"test": "lerna run test"}, workspaces=["packages/*"]),
            "package-lock.json": "{}\n",
            "lerna.json": json.dumps({"version": "independent"}),
            **package_files("node --test"),
        },
        {
            **CODE_EDIT,
            f"{PKG}/project.json": json.dumps(
                {"name": "a", "targets": {"test": {"command": "true"}}}
            ),
        },
    )
    plan = s.plan()
    assert vouched(plan), explain(plan)
