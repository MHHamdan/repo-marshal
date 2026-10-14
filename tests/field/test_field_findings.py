"""Field test on real, unconfigured repositories (MISSION.md §43; docs/evaluation.md).

Each test rebuilds, from a small synthetic fixture under tests/fixtures/field/<case>/, the part of
a real repository that drove a plan, applies the change of a real upstream commit (uncommitted,
BASE = the fixture's first commit) and checks `api.plan`.

- `@pytest.mark.fieldfinding` tests assert the CORRECT behaviour for something RepoMarshal got
  wrong in the field. They fail until it is fixed.
- The unmarked tests pin cases that worked in the field, as regression guards.

Fixture layout: `base/` is committed as BASE; `<variant>/` is copied over it; the optional
`<variant>.deleted` lists paths to delete. Every fixture README names the upstream repository and
commits it mirrors. Tool availability is pinned with stub executables, so no real tool runs.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from repomarshal import api
from repomarshal.model import DecisionStatus, Gate, GateDecision, Plan
from tests.helpers import GitRepo, env_with_path, stub_bin

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "field"
STUB_TOOLS = ("npm", "pnpm", "yarn", "ruff", "mypy", "pyright", "python", "make", "nub")
SELECTED = (DecisionStatus.REQUIRED, DecisionStatus.UNAVAILABLE)
EXPENSIVE_KINDS = ("integration", "runtime", "a11y", "dep_audit", "security_scan", "skill_audit")

fieldfinding = pytest.mark.fieldfinding


@dataclass
class Case:
    repo: GitRepo
    base: str
    env: dict[str, str]

    def plan(self) -> Plan:
        return api.plan(self.repo.root, base=self.base, env=self.env)


def build(tmp_path: Path, fixture: str, variant: str) -> Case:
    """Commit `<fixture>/base` as BASE, then apply `<fixture>/<variant>` uncommitted."""
    src = FIXTURES / fixture
    repo = GitRepo(tmp_path / fixture)
    # shutil.copy, not copy2: a change file with BASE's size and a preserved mtime would look
    # stat-clean to git, which then reads the "old" blob from the working tree.
    shutil.copytree(src / "base", repo.root, dirs_exist_ok=True, copy_function=shutil.copy)
    base = repo.commit("BASE")
    shutil.copytree(src / variant, repo.root, dirs_exist_ok=True, copy_function=shutil.copy)
    deleted = src / f"{variant}.deleted"
    if deleted.exists():
        for rel in deleted.read_text().split():
            repo.delete(rel)
    bindir = tmp_path / "bin"
    for tool in STUB_TOOLS:
        stub_bin(bindir, tool)
    return Case(repo, base, env_with_path(bindir, keep_system=False))


# ---------------------------------------------------------------------- plan helpers


def explain(plan: Plan) -> str:
    rows = [f"{d.gate_id or d.kind}: {d.status}" for d in plan.gates]
    rows += [f"signal {s.id}: {', '.join(s.files)}" for s in plan.signals]
    return "\n".join(rows)


def gate(plan: Plan, gate_id: str) -> Gate:
    found = [g for g in plan.inventory if g.id == gate_id]
    assert len(found) == 1, f"expected one inventory gate {gate_id}\n{explain(plan)}"
    return found[0]


def decisions(plan: Plan, kind: str) -> list[GateDecision]:
    return [d for d in plan.gates if d.kind == kind]


def required_of_kind(plan: Plan, kind: str) -> list[GateDecision]:
    return [d for d in decisions(plan, kind) if d.status == DecisionStatus.REQUIRED]


def status_of(plan: Plan, gate_id: str) -> DecisionStatus:
    found = [d for d in plan.gates if d.gate_id == gate_id]
    assert len(found) == 1, f"expected one decision for {gate_id}\n{explain(plan)}"
    return found[0].status


def signal_ids(plan: Plan) -> set[str]:
    return {s.id for s in plan.signals}


def selected_expensive(plan: Plan) -> list[str]:
    return [
        d.gate_id or str(d.kind)
        for d in plan.gates
        if d.kind in EXPENSIVE_KINDS and d.status in SELECTED
    ]


# ====================================================================== findings (expected to fail)

# ---- inventory: commands that differ from what the repository runs


@fieldfinding
def test_ruff_lint_gate_does_not_apply_fixes(tmp_path):
    """pallets/click sets `[tool.ruff] fix = true`, so `ruff check .` rewrites files: a lint
    failure is silently fixed, and `done` reports the gate UNKNOWN (tree modified). The lint gate
    must check only."""
    plan = build(tmp_path, "click-pyproject", "change-src").plan()
    lint = gate(plan, "root:lint")
    assert "--no-fix" in lint.argv, f"root:lint argv {lint.argv} applies ruff's configured fixes"


@fieldfinding
def test_mypy_gate_respects_configured_files(tmp_path):
    """pallets/click (`files = ["src", "tests/typing"]`) and Textualize/rich (`files = ["rich"]`):
    `mypy .` overrides the configured `files`, so strict mypy also checks tests, docs and examples
    the project never type-checks. With `files` configured the gate must not pass a path."""
    plan = build(tmp_path, "click-pyproject", "change-src").plan()
    mypy = gate(plan, "root:typecheck")
    assert mypy.argv[0] == "mypy"
    positional = [a for a in mypy.argv[1:] if not a.startswith("-")]
    assert positional == [], f"root:typecheck argv {mypy.argv} overrides [tool.mypy] files"


@fieldfinding
def test_lint_gate_prefers_the_non_mutating_check_script(tmp_path):
    """colinhacks/zod: `lint` is `biome lint --write .`, `lint:check` is `biome lint .` (what CI
    runs). The inventory picks `lint`, which rewrites the tree."""
    plan = build(tmp_path, "zod-like", "change-test").plan()
    lint = gate(plan, "root:lint")
    assert lint.argv[-1] == "lint:check", f"root:lint runs {lint.argv}, a --write script"


@fieldfinding
def test_package_manager_field_selects_the_runner(tmp_path):
    """colinhacks/zod declares `"packageManager": "nub@0.8.3"` next to a pnpm-lock.yaml. The
    inventory derives `pnpm run ...` from the lockfile, and pnpm refuses to run in a project
    configured for another package manager (`ERROR This project is configured to use nub`), so
    every script gate would FAIL. The declared package manager must win."""
    plan = build(tmp_path, "zod-like", "change-test").plan()
    for gate_id in ("root:format", "root:lint", "root:unit"):
        argv = gate(plan, gate_id).argv
        assert argv[:2] == ("nub", "run"), f"{gate_id} argv {argv} ignores packageManager"


@fieldfinding
def test_test_prefixed_scripts_are_inventoried(tmp_path):
    """pmndrs/zustand names its checks `test:lint`, `test:types`, `test:format` and `test:spec`
    (CI runs each). Only `test` is found, so lint, typecheck and format do not exist as gates."""
    plan = build(tmp_path, "zustand-like", "change-src").plan()
    expected = {"lint": "test:lint", "typecheck": "test:types", "format": "test:format"}
    for kind, script in expected.items():
        found = [g for g in plan.inventory if g.kind == kind and g.argv[-1:] == (script,)]
        assert found, f"no {kind} gate for script {script!r}\n{explain(plan)}"
        assert status_of(plan, found[0].id) == DecisionStatus.REQUIRED, explain(plan)


@fieldfinding
def test_makefile_targets_are_inventoried(tmp_path):
    """Textualize/rich: CI runs `make format-check` (black) and `make typecheck`. Architecture
    §3.2 lists Makefile targets as a gate source; none is inventoried, so the format check is
    missing from the plan."""
    plan = build(tmp_path, "rich-makefile", "change-src").plan()
    fmt = [g for g in plan.inventory if g.kind == "format"]
    assert [g.argv for g in fmt] == [("make", "format-check")], explain(plan)
    assert status_of(plan, fmt[0].id) == DecisionStatus.REQUIRED, explain(plan)


# ---- inventory: scopes


@fieldfinding
def test_pytest_ini_inside_tests_dir_does_not_duplicate_the_unit_gate(tmp_path):
    """Textualize/rich has `tests/pytest.ini` next to `[tool.pytest.ini_options]` in the root
    pyproject. `tests/` becomes a scope of its own, and the whole suite is required twice
    (root:unit and tests:unit)."""
    plan = build(tmp_path, "rich-makefile", "change-test").plan()
    unit = [d.gate_id for d in required_of_kind(plan, "unit")]
    assert unit == ["root:unit"], explain(plan)


@fieldfinding
def test_workspace_packages_are_gate_scopes(tmp_path):
    """colinhacks/zod (`"workspaces": ["packages/*"]`): `packages/zod/package.json` has its own
    `test` script, but only the root and its immediate subdirectories are scopes, so no
    package-level gate exists."""
    case = build(tmp_path, "zod-like", "change-src")
    plan = case.plan()
    pkg_unit = [g for g in plan.inventory if g.kind == "unit" and g.scope == "packages/zod/"]
    assert len(pkg_unit) == 1, explain(plan)
    assert status_of(plan, pkg_unit[0].id) == DecisionStatus.REQUIRED, explain(plan)


# ---- signals and decisions


@fieldfinding
def test_dependency_bump_raises_dependency_changed(tmp_path):
    """expressjs/express 3b7e39f bumps `qs` in package.json. No dependency.changed fires (the
    file is only coverage.unclassified), so the dependency audit is not demanded."""
    plan = build(tmp_path, "express-like", "change-deps").plan()
    assert "dependency.changed" in signal_ids(plan), explain(plan)
    assert "coverage.unclassified" not in signal_ids(plan), explain(plan)
    statuses = {d.status for d in decisions(plan, "dep_audit")}
    assert statuses == {DecisionStatus.UNAVAILABLE}, explain(plan)


@fieldfinding
def test_lockfile_change_raises_dependency_changed(tmp_path):
    """colinhacks/zod 59bbc03 changes only pnpm-lock.yaml (also: click uv.lock, zustand
    pnpm-lock.yaml). A lockfile is a dependency change, not an unclassified file."""
    plan = build(tmp_path, "zod-like", "change-lockfile").plan()
    assert "dependency.changed" in signal_ids(plan), explain(plan)
    assert "coverage.unclassified" not in signal_ids(plan), explain(plan)


@fieldfinding
def test_tsx_test_file_does_not_require_runtime_or_a11y(tmp_path):
    """pmndrs/zustand 3febf8c changes `src/middleware/persist.ts` and a `.test.tsx` test. The test
    file's extension fires frontend.ui, so runtime and a11y become UNAVAILABLE and `done` can
    never PASS, although no shipped UI changed."""
    plan = build(tmp_path, "zustand-like", "change-src").plan()
    assert selected_expensive(plan) == [], explain(plan)


@fieldfinding
def test_ci_workflow_only_change_does_not_demand_integration_tests(tmp_path):
    """pallets/click 0c9e836 (and express 3ce6d0e, rich eccb594, zustand f094eeb) only edits
    workflow files. ci.changed raises risk to elevated, which demands integration tests; none of
    the five repositories has an integration gate, so every CI-only change is UNKNOWN for a
    reason (integration tests) that cannot verify a workflow file. Rule-level finding: if the
    UNKNOWN is intended, the reason should name CI, not integration tests."""
    plan = build(tmp_path, "click-pyproject", "change-ci").plan()
    integration = decisions(plan, "integration")
    assert all(d.status not in SELECTED for d in integration), explain(plan)


@fieldfinding
def test_script_run_by_ci_under_dot_github_is_ci_changed(tmp_path):
    """colinhacks/zod 9c21983 edits `.github/scripts/npm-stage-publish.sh`, which the release
    workflow runs. Only `.github/workflows/` counts as CI, so the change is standard risk."""
    plan = build(tmp_path, "zod-like", "change-ci-script").plan()
    assert "ci.changed" in signal_ids(plan), explain(plan)
    assert plan.risk == "elevated", explain(plan)


# ---- context


@fieldfinding
def test_lockfile_is_not_full_text_context(tmp_path):
    """colinhacks/zod f6e1701 put pnpm-lock.yaml into the context at ~108,000 estimated tokens
    (click 3b16957: uv.lock ~64,600; zustand 1f531ba: pnpm-lock.yaml ~45,400), nine times the
    12,000-token budget, because every changed file is a P1 item. A generated lockfile is not
    something an agent should read in full."""
    plan = build(tmp_path, "zod-like", "change-lockfile").plan()
    paths = {i.path for i in plan.context.items}
    assert "pnpm-lock.yaml" not in paths, sorted(paths)


# ====================================================================== regression guards (pass)


def test_docs_only_change_requires_only_docs_links(tmp_path):
    """pallets/click fa99fbd (also rich ce51a2f, express 53d4a0d, zustand b57db4f)."""
    plan = build(tmp_path, "click-pyproject", "change-docs").plan()
    required = {d.gate_id or d.kind for d in plan.gates if d.status in SELECTED}
    assert required == {"native:docs-links"}, explain(plan)
    assert plan.risk == "low", explain(plan)


def test_python_source_change_requires_lint_typecheck_unit_only(tmp_path):
    """pallets/click fc518e4: the cheap and standard gates of the scope, no expensive gate."""
    plan = build(tmp_path, "click-pyproject", "change-src").plan()
    for gate_id in ("root:lint", "root:typecheck", "root:unit"):
        assert status_of(plan, gate_id) == DecisionStatus.REQUIRED, explain(plan)
    assert selected_expensive(plan) == [], explain(plan)
    assert status_of(plan, "native:secrets-diff") == DecisionStatus.SKIPPED, explain(plan)


def test_tests_moved_into_new_files_are_not_deleted(tmp_path):
    """pallets/click b8a4486 splits tests/test_types.py into tests/test_types/*.py: a move, not a
    deletion, so no test-tamper signal and no taint."""
    plan = build(tmp_path, "click-pyproject", "change-tests-split").plan()
    assert not signal_ids(plan) & {"tests.deleted", "tests.skip_added"}, explain(plan)
    assert status_of(plan, "root:unit") == DecisionStatus.REQUIRED, explain(plan)
    assert all(not d.taint for d in plan.gates), explain(plan)


def test_js_source_and_test_change_requires_lint_and_unit(tmp_path):
    """expressjs/express 9a34acf (also b4ab7d6, test-only)."""
    plan = build(tmp_path, "express-like", "change-src").plan()
    assert status_of(plan, "root:lint") == DecisionStatus.REQUIRED, explain(plan)
    assert status_of(plan, "root:unit") == DecisionStatus.REQUIRED, explain(plan)
    assert gate(plan, "root:unit").argv == ("npm", "run", "test")
    assert selected_expensive(plan) == [], explain(plan)


def test_npmrc_change_widens_to_lint_and_unit(tmp_path):
    """expressjs/express 2eae22b: `.npmrc` is build configuration (standard risk)."""
    plan = build(tmp_path, "express-like", "change-npmrc").plan()
    assert "build.config_changed" in signal_ids(plan), explain(plan)
    assert status_of(plan, "root:lint") == DecisionStatus.REQUIRED, explain(plan)
    assert status_of(plan, "root:unit") == DecisionStatus.REQUIRED, explain(plan)
    assert plan.risk == "standard", explain(plan)


def test_dependency_only_package_json_edit_is_not_build_config(tmp_path):
    """expressjs/express 3b7e39f: the dependency sections are discounted from
    build.config_changed (the change still widens through another signal)."""
    plan = build(tmp_path, "express-like", "change-deps").plan()
    assert "build.config_changed" not in signal_ids(plan), explain(plan)
    assert status_of(plan, "root:unit") == DecisionStatus.REQUIRED, explain(plan)


def test_ts_test_only_change_in_monorepo(tmp_path):
    """colinhacks/zod 07917f4: a `.test.ts` change needs format, lint and unit, nothing
    expensive."""
    plan = build(tmp_path, "zod-like", "change-test").plan()
    for gate_id in ("root:format", "root:lint", "root:unit"):
        assert status_of(plan, gate_id) == DecisionStatus.REQUIRED, explain(plan)
    assert selected_expensive(plan) == [], explain(plan)
    assert gate(plan, "root:format").argv[-1] == "format:check"


def test_frontend_component_change_demands_runtime_and_a11y(tmp_path):
    """colinhacks/zod e54716c edits a docs-site React component: no runtime or a11y gate exists,
    so both are honestly UNAVAILABLE rather than skipped."""
    plan = build(tmp_path, "zod-like", "change-site").plan()
    for kind in ("runtime", "a11y"):
        statuses = {d.status for d in decisions(plan, kind)}
        assert statuses == {DecisionStatus.UNAVAILABLE}, explain(plan)


def test_ci_script_with_token_line_requires_secrets_scan(tmp_path):
    """colinhacks/zod 9c21983: an added line mentions the OIDC token step, so the secrets
    baseline runs even though the CI classification is missed (see the fieldfinding above)."""
    plan = build(tmp_path, "zod-like", "change-ci-script").plan()
    assert status_of(plan, "native:secrets-diff") == DecisionStatus.REQUIRED, explain(plan)
