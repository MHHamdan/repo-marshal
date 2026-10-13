"""Unit tests for repomarshal.inventory (roadmap M3, M14)."""

from __future__ import annotations

import json
import os
import sys

import pytest

from repomarshal.inventory import build_inventory
from repomarshal.model import KIND_ORDER, Cost, GateKind, Policy
from repomarshal.policy import load_policy
from tests.helpers import GitRepo, stub_bin

FRONTEND_SCRIPTS = {
    "dev": "vite",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:e2e": "playwright test",
    "test:a11y": "playwright test --grep @a11y",
}
PYPROJECT = """\
[project]
name = "backend"
dependencies = ["fastapi==0.110.0"]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
ALL_TOOLS = ("npm", "pnpm", "yarn", "ruff", "mypy", "pyright", "python", "bandit")


def _package_json(scripts: dict, **extra) -> str:
    return json.dumps({"name": "frontend", "scripts": scripts, **extra}, indent=2) + "\n"


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    for tool in ALL_TOOLS:
        stub_bin(d, tool)
    return d


def _webapp(tmp_path) -> tuple[GitRepo, str]:
    """The architecture §3.4 scenario (without AGENTS.md, a later inventory source)."""
    repo = GitRepo(tmp_path / "webapp")
    repo.write("docs/install.md", "# Install\n")
    repo.write("frontend/package.json", _package_json(FRONTEND_SCRIPTS))
    repo.write("frontend/package-lock.json", "{}\n")
    repo.write("frontend/src/components/LoginForm.tsx", "export {}\n")
    repo.write("backend/pyproject.toml", PYPROJECT)
    repo.write("backend/app/auth/refresh.py", "x = 1\n")
    repo.write("backend/tests/test_refresh.py", "def test_x():\n    pass\n")
    repo.write("backend/tests/integration/test_auth_flow.py", "def test_y():\n    pass\n")
    return repo, repo.commit("base")


def _inv(repo, base, path_env, policy=None):
    policy = policy if policy is not None else load_policy(repo.root, base)
    return build_inventory(repo.root, base, policy, path_env=str(path_env))


def _by_id(inv):
    return {g.id: g for g in inv.gates}


def _changed(inv):
    return sorted(g.id for g in inv.gates if g.definition_changed)


# --------------------------------------------------------------------------- M3 inventory


def test_webapp_scenario_inventory(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    inv = _inv(repo, base, bin_dir)
    assert inv.scopes == ("backend/", "frontend/")
    rows = [(g.id, g.kind, g.argv, g.scope, g.cwd, g.cost, g.source) for g in inv.gates]
    assert rows == [
        (
            "native:docs-links",
            GateKind.DOCS,
            (),
            "",
            "",
            Cost.CHEAP,
            "builtin",
        ),
        ("native:secrets-diff", GateKind.SECRETS, (), "", "", Cost.CHEAP, "builtin"),
        (
            "backend:lint",
            GateKind.LINT,
            ("ruff", "check", "."),
            "backend/",
            "backend",
            Cost.CHEAP,
            "backend/pyproject.toml#tool.ruff",
        ),
        (
            "backend:typecheck",
            GateKind.TYPECHECK,
            ("mypy", "."),
            "backend/",
            "backend",
            Cost.STANDARD,
            "backend/pyproject.toml#tool.mypy",
        ),
        (
            "backend:unit",
            GateKind.UNIT,
            ("python", "-m", "pytest", "-q", "--ignore=tests/integration"),
            "backend/",
            "backend",
            Cost.STANDARD,
            "backend/pyproject.toml#tool.pytest.ini_options",
        ),
        (
            "backend:integration",
            GateKind.INTEGRATION,
            ("python", "-m", "pytest", "-q", "tests/integration"),
            "backend/",
            "backend",
            Cost.EXPENSIVE,
            "backend/tests/integration/",
        ),
        (
            "frontend:lint",
            GateKind.LINT,
            ("npm", "run", "lint"),
            "frontend/",
            "frontend",
            Cost.CHEAP,
            "frontend/package.json#scripts.lint",
        ),
        (
            "frontend:typecheck",
            GateKind.TYPECHECK,
            ("npm", "run", "typecheck"),
            "frontend/",
            "frontend",
            Cost.STANDARD,
            "frontend/package.json#scripts.typecheck",
        ),
        (
            "frontend:unit",
            GateKind.UNIT,
            ("npm", "run", "test"),
            "frontend/",
            "frontend",
            Cost.STANDARD,
            "frontend/package.json#scripts.test",
        ),
        (
            "frontend:runtime",
            GateKind.RUNTIME,
            ("npm", "run", "test:e2e"),
            "frontend/",
            "frontend",
            Cost.EXPENSIVE,
            "frontend/package.json#scripts.test:e2e",
        ),
        (
            "frontend:a11y",
            GateKind.A11Y,
            ("npm", "run", "test:a11y"),
            "frontend/",
            "frontend",
            Cost.EXPENSIVE,
            "frontend/package.json#scripts.test:a11y",
        ),
    ]
    assert all(g.available for g in inv.gates)
    assert _changed(inv) == []
    assert all(g.definition_digest for g in inv.gates if g.provider == "command")
    assert not any(g.kind == GateKind.SECURITY_SCAN for g in inv.gates)


def test_sorted_and_deterministic(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    a, b = _inv(repo, base, bin_dir), _inv(repo, base, bin_dir)
    assert a == b
    keys = [(g.scope, KIND_ORDER.index(g.kind), g.id) for g in a.gates]
    assert keys == sorted(keys)


def test_inventory_read_at_base_not_worktree(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write("frontend/package.json", _package_json({**FRONTEND_SCRIPTS, "tsc": "x"}))
    repo.write("api/pyproject.toml", "[tool.ruff]\n")  # new scope, uncommitted
    repo.delete("backend/pyproject.toml")
    inv = _inv(repo, base, bin_dir)
    ids = set(_by_id(inv))
    assert "api:lint" not in ids
    assert {"backend:lint", "backend:unit", "frontend:unit"} <= ids
    assert inv.scopes == ("backend/", "frontend/")


def test_no_base_only_native_and_policy(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"test": "jest"}))
    inv = _inv(repo, None, bin_dir, Policy())
    assert [g.id for g in inv.gates] == ["native:docs-links", "native:secrets-diff"]
    assert inv.scopes == ()


def test_root_scope_and_lockfile_package_manager(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"test:unit": "vitest", "format:check": "p -c ."}))
    repo.write("pnpm-lock.yaml", "lockfileVersion: 9\n")
    repo.write("web/package.json", _package_json({"lint": "eslint", "e2e": "cypress run"}))
    repo.write("web/yarn.lock", "")
    repo.write("site/package.json", _package_json({"type-check": "tsc"}))
    repo.write("site/bun.lockb", b"\x00")
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["root:unit"].argv == ("pnpm", "run", "test:unit")
    assert g["root:unit"].cwd == "" and g["root:unit"].scope == ""
    assert g["root:format"].argv == ("pnpm", "run", "format:check")
    assert g["web:lint"].argv == ("yarn", "run", "lint")
    assert g["web:runtime"].argv == ("yarn", "run", "e2e")
    assert g["site:typecheck"].argv == ("bun", "run", "type-check")
    assert not g["site:typecheck"].available  # no bun stub on PATH
    assert g["site:typecheck"].unavailable_reason == "bun not found on PATH"


def test_npm_placeholder_test_ignored(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    placeholder = 'echo "Error: no test specified" && exit 1'
    repo.write("package.json", _package_json({"test": placeholder, "lint": "eslint"}))
    base = repo.commit()
    ids = set(_by_id(_inv(repo, base, bin_dir)))
    assert "root:unit" not in ids and "root:lint" in ids


def test_malformed_manifests_are_skipped(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("a/package.json", "{not json")
    repo.write("b/pyproject.toml", "[tool.ruff\n")
    repo.write("c/package.json", "[1, 2]")
    repo.write("d/package.json", json.dumps({"scripts": ["lint"]}))
    repo.write("e/setup.cfg", "no section header\n")
    repo.write("f/package.json", b"\xff\xfe")
    base = repo.commit()
    inv = _inv(repo, base, bin_dir)
    assert [g.id for g in inv.gates] == ["native:docs-links", "native:secrets-diff"]


def test_python_dedicated_files_and_setup_cfg(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("svc/ruff.toml", "line-length = 90\n")
    repo.write("svc/mypy.ini", "[mypy]\nstrict = True\n")
    repo.write("svc/pytest.ini", "[pytest]\n")
    repo.write("lib/setup.cfg", "[mypy]\nstrict = True\n\n[tool:pytest]\naddopts = -q\n")
    repo.write("pr/pyproject.toml", "[tool.pyright]\nstrict = ['.']\n")
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert (g["svc:lint"].source, g["svc:typecheck"].source, g["svc:unit"].source) == (
        "svc/ruff.toml",
        "svc/mypy.ini",
        "svc/pytest.ini",
    )
    assert g["svc:unit"].argv == ("python", "-m", "pytest", "-q")
    assert g["lib:typecheck"].source == "lib/setup.cfg#mypy"
    assert g["lib:unit"].source == "lib/setup.cfg#tool:pytest"
    assert g["pr:typecheck"].argv == ("pyright",)
    assert "pr:unit" not in g and "lib:lint" not in g


def test_unit_from_tests_dir_without_pytest_config(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", '[project]\nname = "x"\n')
    repo.write("tests/test_a.py", "")
    repo.write("ui/package.json", _package_json({"lint": "eslint"}))
    repo.write("ui/tests/test_b.py", "")  # not a python scope: no pytest gate
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["root:unit"].argv == ("python", "-m", "pytest", "-q")
    assert g["root:unit"].source == "tests/"
    assert "ui:unit" not in g


def test_integration_only_with_python_unit(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", '[project]\nname = "x"\n')
    repo.write("tests/integration/test_flow.py", "")
    base = repo.commit()
    assert "root:integration" not in _by_id(_inv(repo, base, bin_dir))


def test_policy_gate_overrides_same_scope_and_kind(tmp_path, bin_dir):
    repo, _ = _webapp(tmp_path)
    repo.write(
        "repomarshal.toml",
        """\
version = 1
[gates.fe-lint]
kind = "lint"
argv = ["pnpm", "lint"]
scope = "frontend/"
[gates.sec]
kind = "security_scan"
argv = ["bandit", "-r", "app"]
scope = "backend/"
""",
    )
    base = repo.commit("policy")
    g = _by_id(_inv(repo, base, bin_dir))
    assert "frontend:lint" not in g
    assert g["fe-lint"].argv == ("pnpm", "lint")
    assert g["fe-lint"].source == "repomarshal.toml#gates.fe-lint"
    assert g["sec"].kind == GateKind.SECURITY_SCAN and g["sec"].available
    assert "backend:lint" in g
    assert _changed(_inv(repo, base, bin_dir)) == []


# --------------------------------------------------------------------------- availability


def test_unavailable_tools(tmp_path):
    repo, base = _webapp(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    g = _by_id(_inv(repo, base, empty))
    assert not g["backend:lint"].available
    assert g["backend:lint"].unavailable_reason == "ruff not found on PATH"
    assert g["frontend:unit"].unavailable_reason == "npm not found on PATH"
    assert g["native:docs-links"].available  # native gates need nothing


def test_python_falls_back_to_python3(tmp_path):
    repo, base = _webapp(tmp_path)
    d = tmp_path / "py3"
    stub_bin(d, "python3")
    g = _by_id(_inv(repo, base, d))["backend:unit"]
    assert g.available
    assert g.argv[0] == "python3"
    assert g.argv[1:] == ("-m", "pytest", "-q", "--ignore=tests/integration")


def test_python_falls_back_to_sys_executable_name(tmp_path):
    repo, base = _webapp(tmp_path)
    name = os.path.basename(sys.executable)
    d = tmp_path / "pyx"
    stub_bin(d, name)
    g = _by_id(_inv(repo, base, d))["backend:unit"]
    assert g.available
    assert g.argv[0] == name


def test_relative_argv0_resolved_in_gate_cwd(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write(
        "repomarshal.toml",
        'version = 1\n[gates.chk]\nkind = "lint"\nargv = ["./check.sh"]\nscope = "app/"\n'
        '[gates.miss]\nkind = "unit"\nargv = ["./nope.sh"]\n',
    )
    stub_bin(repo.root / "app", "check.sh")
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["chk"].available
    assert not g["miss"].available


# --------------------------------------------------------------------------- M14


def test_m14_test_script_exit_0_attack(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    before = _by_id(_inv(repo, base, bin_dir))["frontend:unit"]
    repo.write("frontend/package.json", _package_json({**FRONTEND_SCRIPTS, "test": "exit 0"}))
    inv = _inv(repo, base, bin_dir)
    unit = _by_id(inv)["frontend:unit"]
    assert unit.definition_changed
    assert unit.argv == ("npm", "run", "test")  # still the BASE definition
    assert unit.definition_digest == before.definition_digest  # digest is of BASE
    assert _changed(inv) == ["frontend:unit"]


def test_m14_committed_attack_is_still_detected(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write("frontend/package.json", _package_json({**FRONTEND_SCRIPTS, "test": "exit 0"}))
    repo.commit("sneaky")
    assert _changed(_inv(repo, base, bin_dir)) == ["frontend:unit"]


def test_m14_unrelated_package_json_change(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write(
        "frontend/package.json",
        _package_json({**FRONTEND_SCRIPTS, "dev": "vite --host"}, dependencies={"react": "19"}),
    )
    assert _changed(_inv(repo, base, bin_dir)) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda r: r.write(
                "frontend/package.json",
                _package_json({k: v for k, v in FRONTEND_SCRIPTS.items() if k != "lint"}),
            ),
            ["frontend:lint"],
        ),
        (
            lambda r: r.write(
                "frontend/package.json", _package_json({**FRONTEND_SCRIPTS, "pretest": "rm -rf t"})
            ),
            ["frontend:unit"],
        ),
        (lambda r: r.write("frontend/package.json", "{broken"), "all-frontend"),
        (lambda r: r.delete("frontend/package.json"), "all-frontend"),
        (lambda r: r.write("frontend/pnpm-lock.yaml", ""), "all-frontend"),
    ],
    ids=["script-removed", "pre-hook-added", "malformed", "deleted", "package-manager-changed"],
)
def test_m14_package_json_changes(tmp_path, bin_dir, mutate, expected):
    repo, base = _webapp(tmp_path)
    mutate(repo)
    changed = _changed(_inv(repo, base, bin_dir))
    if expected == "all-frontend":
        expected = sorted(f"frontend:{k}" for k in ("lint", "typecheck", "unit", "runtime", "a11y"))
    assert changed == expected


def test_m14_pyproject_tool_tables(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write("backend/pyproject.toml", PYPROJECT.replace("0.110.0", "0.111.0"))
    assert _changed(_inv(repo, base, bin_dir)) == []

    repo.write("backend/pyproject.toml", PYPROJECT.replace("strict = true", "strict = false"))
    assert _changed(_inv(repo, base, bin_dir)) == ["backend:typecheck"]

    repo.write(
        "backend/pyproject.toml",
        PYPROJECT.replace('testpaths = ["tests"]', 'testpaths = ["nothing"]'),
    )
    assert _changed(_inv(repo, base, bin_dir)) == ["backend:integration", "backend:unit"]

    repo.write("backend/pyproject.toml", PYPROJECT.replace("[tool.ruff]\nline-length = 100\n", ""))
    assert _changed(_inv(repo, base, bin_dir)) == ["backend:lint"]


def test_m14_higher_precedence_config_added(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write("backend/ruff.toml", "exclude = ['*']\n")
    repo.write("backend/pytest.ini", "[pytest]\naddopts = --collect-only\n")
    assert _changed(_inv(repo, base, bin_dir)) == [
        "backend:integration",
        "backend:lint",
        "backend:unit",
    ]


def test_m14_pyproject_deleted_or_malformed(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    expected = ["backend:integration", "backend:lint", "backend:typecheck", "backend:unit"]
    repo.write("backend/pyproject.toml", "[tool.ruff\n")
    assert _changed(_inv(repo, base, bin_dir)) == expected
    repo.delete("backend/pyproject.toml")
    assert _changed(_inv(repo, base, bin_dir)) == expected


def test_m14_dedicated_file_is_whole_definition(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("ruff.toml", "line-length = 90\n")
    repo.write("mypy.ini", "[mypy]\n")
    base = repo.commit()
    assert _changed(_inv(repo, base, bin_dir)) == []
    repo.write("ruff.toml", "line-length = 90\n# comment\n")
    assert _changed(_inv(repo, base, bin_dir)) == ["root:lint"]


def test_m14_tests_dir_unit_gate_gains_config(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", '[project]\nname = "x"\n')
    repo.write("tests/test_a.py", "")
    base = repo.commit()
    repo.write("tests/test_a.py", "def test_new(): pass\n")  # test edits are not definitions
    assert _changed(_inv(repo, base, bin_dir)) == []
    repo.write(
        "pyproject.toml", '[project]\nname = "x"\n[tool.pytest.ini_options]\naddopts = "-k none"\n'
    )
    assert _changed(_inv(repo, base, bin_dir)) == ["root:unit"]


POLICY = """\
version = 1
[gates.sec]
kind = "security_scan"
argv = ["bandit", "-r", "app"]
[gates.audit]
kind = "dep_audit"
argv = ["pip-audit"]
"""


def test_m14_policy_gate_tables(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("repomarshal.toml", POLICY)
    base = repo.commit()
    assert _changed(_inv(repo, base, bin_dir)) == []

    # Reformatting and unrelated edits to the file keep the tables equal.
    repo.write("repomarshal.toml", "# tweak\n" + POLICY + "[skills]\nmax = 1\n")
    assert _changed(_inv(repo, base, bin_dir)) == []

    repo.write("repomarshal.toml", POLICY.replace('"-r", "app"', '"--version"'))
    assert _changed(_inv(repo, base, bin_dir)) == ["sec"]

    repo.write("repomarshal.toml", "version = 1\n")
    assert _changed(_inv(repo, base, bin_dir)) == ["audit", "sec"]

    repo.write("repomarshal.toml", "garbage = [")
    assert _changed(_inv(repo, base, bin_dir)) == ["audit", "sec"]

    repo.delete("repomarshal.toml")
    assert _changed(_inv(repo, base, bin_dir)) == ["audit", "sec"]


def test_m14_native_gates_never_change(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.delete("frontend/package.json")
    repo.delete("backend/pyproject.toml")
    for g in _inv(repo, base, bin_dir).gates:
        if g.provider == "native":
            assert not g.definition_changed
            assert g.definition_digest is None
