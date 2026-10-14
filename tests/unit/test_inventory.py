"""Unit tests for repomarshal.inventory (roadmap M3, M14)."""

from __future__ import annotations

import json
import os
import sys

import pytest

from repomarshal import git, inventory
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
ALL_TOOLS = ("npm", "pnpm", "yarn", "ruff", "mypy", "pyright", "python", "bandit", "make")


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
            ("ruff", "check", "--no-fix", "."),
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


def test_pytest_toml_is_a_gate_source_with_pytest_precedence(tmp_path, bin_dir):
    # pytest >= 9: pytest.toml / .pytest.toml ([pytest] table) beat every other config file.
    repo = GitRepo(tmp_path / "r")
    repo.write("svc/pytest.toml", "[pytest]\naddopts = ['-q']\n")
    repo.write("svc/pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-x"\n')
    repo.write("hid/.pytest.toml", "[pytest]\n")
    repo.write("ini/.pytest.ini", "[pytest]\n")
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["svc:unit"].source == "svc/pytest.toml"
    assert g["hid:unit"].source == "hid/.pytest.toml"
    assert g["ini:unit"].source == "ini/.pytest.ini"
    assert inventory.detect_scopes(("a/pytest.toml", "b/.pytest.toml")) == ("a/", "b/")


def test_m14_pytest_toml_added_changes_the_definition_digest(tmp_path, bin_dir):
    # Even without a change set, the worktree definition now resolves to pytest.toml.
    repo, base = _webapp(tmp_path)
    repo.write("backend/pytest.toml", '[pytest]\naddopts = ["-k", "not expired"]\n')
    assert _changed(_inv(repo, base, bin_dir)) == ["backend:integration", "backend:unit"]


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


# --------------------------------------------------------------------------- M14 definition files


def _inv_c(repo, base, path_env, policy=None):
    """Inventory with the change set relative to BASE (as the planner builds it)."""
    policy = policy if policy is not None else load_policy(repo.root, base)
    change = git.collect_changes(repo.root, base)
    return build_inventory(repo.root, base, policy, path_env=str(path_env), change=change)


BACKEND_PYTEST = ["backend:integration", "backend:unit"]
ALL_FRONTEND = sorted(f"frontend:{k}" for k in ("lint", "typecheck", "unit", "runtime", "a11y"))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("backend/.pytest.ini", BACKEND_PYTEST),
        ("backend/pytest.toml", BACKEND_PYTEST),
        ("backend/tests/.pytest.toml", BACKEND_PYTEST),
        ("backend/tests/conftest.py", BACKEND_PYTEST),
        ("backend/tests/integration/conftest.py", BACKEND_PYTEST),
        ("backend/tox.ini", BACKEND_PYTEST),
        ("backend/app/ruff.toml", ["backend:lint"]),
        ("backend/app/.ruff.toml", ["backend:lint"]),
        ("backend/mypy.ini", ["backend:typecheck"]),
        ("backend/setup.cfg", ["backend:integration", "backend:typecheck", "backend:unit"]),
        ("frontend/.npmrc", ALL_FRONTEND),
        (".npmrc", ALL_FRONTEND),
        ("frontend/eslint.config.js", ALL_FRONTEND),
        ("frontend/src/.eslintrc.json", ALL_FRONTEND),
        ("frontend/tsconfig.json", ALL_FRONTEND),
        ("frontend/tsconfig.build.json", ALL_FRONTEND),
        ("frontend/vitest.config.ts", ALL_FRONTEND),
        ("frontend/e2e/playwright.config.ts", ALL_FRONTEND),
        ("frontend/.babelrc", ALL_FRONTEND),
    ],
)
def test_m14_tool_config_file_added(tmp_path, bin_dir, path, expected):
    repo, base = _webapp(tmp_path)
    repo.write(path, "x = 1\n")
    inv = _inv_c(repo, base, bin_dir)
    assert _changed(inv) == expected
    for gate_id in expected:
        assert _by_id(inv)[gate_id].source.endswith(f"; definition changed: {path}")


def test_m14_tool_config_modified_deleted_and_renamed(tmp_path, bin_dir):
    repo, _ = _webapp(tmp_path)
    repo.write("frontend/tsconfig.json", '{"compilerOptions": {"strict": true}}\n')
    repo.write("backend/tests/conftest.py", "import pytest\n")
    base = repo.commit("configs")
    assert _changed(_inv_c(repo, base, bin_dir)) == []

    repo.write("frontend/tsconfig.json", '{"compilerOptions": {"strict": false}}\n')
    assert _changed(_inv_c(repo, base, bin_dir)) == ALL_FRONTEND

    repo.delete("frontend/tsconfig.json")
    assert _changed(_inv_c(repo, base, bin_dir)) == ALL_FRONTEND

    # Renamed out of the definition set: the old path still counts.
    repo.write("frontend/tsconfig.json", '{"compilerOptions": {"strict": true}}\n')
    repo.git("mv", "backend/tests/conftest.py", "backend/tests/helpers_conftest.py")
    change = git.collect_changes(repo.root, base)
    assert any(f.old_path == "backend/tests/conftest.py" for f in change.files)
    assert _changed(_inv_c(repo, base, bin_dir)) == BACKEND_PYTEST


@pytest.mark.parametrize(
    "path",
    [
        "frontend/src/components/LoginForm.tsx",
        "backend/app/auth/refresh.py",
        "backend/tests/test_refresh.py",
        "frontend/node_modules/pkg/tsconfig.json",
        "frontend/node_modules/.bin/eslint.config.js",
        ".repomarshal/conftest.py",
        "docs/tsconfig.json",  # outside the frontend scope
        "backend/sub/mypy.ini",  # mypy reads its config from the cwd only
        "frontend/.npmrc.bak",
    ],
)
def test_m14_ordinary_edits_are_not_definition_changes(tmp_path, bin_dir, path):
    repo, base = _webapp(tmp_path)
    repo.write(path, "x = 1\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == []


def test_m14_dependency_bump_still_semantic(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write("frontend/package.json", _package_json(FRONTEND_SCRIPTS, dependencies={"r": "1"}))
    repo.write("backend/pyproject.toml", PYPROJECT.replace("0.110.0", "0.111.0"))
    assert _changed(_inv_c(repo, base, bin_dir)) == []


def test_m14_pyright_config_file(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", '[project]\nname = "x"\n[tool.pyright]\nstrict = ["."]\n')
    base = repo.commit()
    repo.write("pyrightconfig.json", '{"exclude": ["**"]}\n')
    assert _changed(_inv_c(repo, base, bin_dir)) == ["root:typecheck"]


def test_m14_script_executes_edited_repo_file(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    scripts = {
        "lint": "eslint --config=tools/lint.cjs .",
        "test": "node scripts/test.js && ./tools/check.sh",
        "pretypecheck": "sh ../outside.sh",
        "typecheck": "tsc --noEmit -p 'cfg/types.json'",
    }
    repo.write("web/package.json", _package_json(scripts))
    for f in ("scripts/test.js", "tools/check.sh", "tools/lint.cjs", "cfg/types.json", "a.js"):
        repo.write(f"web/{f}", "// base\n")
    repo.write("outside.sh", "exit 1\n")
    base = repo.commit()

    repo.write("web/a.js", "// edited\n")  # not named by any script
    repo.write("outside.sh", "exit 0\n")  # pretypecheck runs ../outside.sh: inside the repo
    assert _changed(_inv_c(repo, base, bin_dir)) == ["web:typecheck"]

    repo.git("checkout", "--", "outside.sh")
    repo.write("web/scripts/test.js", "process.exit(0)\n")
    inv = _inv_c(repo, base, bin_dir)
    assert _changed(inv) == ["web:unit"]
    assert _by_id(inv)["web:unit"].source == (
        "web/package.json#scripts.test; definition changed: web/scripts/test.js"
    )

    repo.git("checkout", "--", "web/scripts/test.js")
    repo.delete("web/tools/check.sh")
    repo.write("web/tools/lint.cjs", "module.exports = {}\n")
    repo.write("web/cfg/types.json", "{}\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == ["web:lint", "web:typecheck", "web:unit"]


def test_m14_unparsable_script_falls_back_to_whitespace(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"test": "node run.js 'unterminated"}))
    repo.write("run.js", "// base\n")
    base = repo.commit()
    repo.write("run.js", "process.exit(0)\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == ["root:unit"]


def test_m14_policy_gate_executes_edited_repo_file(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write(
        "repomarshal.toml",
        'version = 1\n[gates.check]\nkind = "unit"\nscope = "backend/"\n'
        'argv = ["./scripts/check.sh", "--strict"]\n'
        '[gates.other]\nkind = "lint"\nargv = ["bandit", "-r", "app"]\n',
    )
    repo.write("backend/scripts/check.sh", "exit 1\n")
    repo.write("backend/app/x.py", "x = 1\n")
    base = repo.commit()
    repo.write("backend/app/x.py", "x = 2\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == []
    repo.write("backend/scripts/check.sh", "exit 0\n")
    inv = _inv_c(repo, base, bin_dir)
    assert _changed(inv) == ["check"]
    assert _by_id(inv)["check"].source.endswith("; definition changed: backend/scripts/check.sh")


def test_m14_without_change_set_only_digests_count(tmp_path, bin_dir):
    repo, base = _webapp(tmp_path)
    repo.write("frontend/tsconfig.json", "{}\n")
    assert _changed(_inv(repo, base, bin_dir)) == []  # change=None: legacy behaviour


# --------------------------------------------------------------------------- gate ids


def test_subdirectory_named_root_gets_distinct_ids(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    scripts = {"lint": "eslint .", "test": "jest"}
    repo.write("package.json", _package_json(scripts))
    repo.write("Root/package.json", _package_json(scripts))
    base = repo.commit()
    ids = {g.id for g in _inv(repo, base, bin_dir).gates if g.provider == "command"}
    assert ids == {"root:lint", "root:unit", "Root/:lint", "Root/:unit"}


def test_duplicate_gate_ids_raise(tmp_path, bin_dir, monkeypatch):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"lint": "eslint ."}))
    repo.write("root/package.json", _package_json({"lint": "eslint ."}))
    base = repo.commit()
    monkeypatch.setattr(inventory, "_scope_name", lambda scope: scope[:-1] if scope else "root")
    with pytest.raises(ValueError, match="duplicate gate ids: root:lint"):
        _inv(repo, base, bin_dir)


# --------------------------------------------------------------------------- field findings


def _argv(inv):
    return {g.id: g.argv for g in inv.gates if g.provider == "command"}


def test_ruff_gate_never_applies_configured_fixes(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", "[tool.ruff]\nfix = true\n")
    repo.write("sub/ruff.toml", "fix = true\n")
    base = repo.commit()
    argv = _argv(_inv(repo, base, bin_dir))
    assert argv["root:lint"] == argv["sub:lint"] == ("ruff", "check", "--no-fix", ".")


@pytest.mark.parametrize(
    ("path", "content", "expected"),
    [
        ("pyproject.toml", '[tool.mypy]\nfiles = ["src"]\n', ("mypy",)),
        ("pyproject.toml", '[tool.mypy]\npackages = ["pkg"]\n', ("mypy",)),
        ("pyproject.toml", '[tool.mypy]\nmodules = ["m"]\n', ("mypy",)),
        ("pyproject.toml", "[tool.mypy]\nfiles = []\n", ("mypy", ".")),
        ("pyproject.toml", "[tool.mypy]\nstrict = true\n", ("mypy", ".")),
        ("mypy.ini", "[mypy]\nfiles = src, tests\n", ("mypy",)),
        (".mypy.ini", "[mypy]\nstrict = True\n", ("mypy", ".")),
        ("setup.cfg", "[mypy]\npackages = pkg\n", ("mypy",)),
    ],
)
def test_mypy_gate_respects_configured_targets(tmp_path, bin_dir, path, content, expected):
    repo = GitRepo(tmp_path / "r")
    repo.write(path, content)
    base = repo.commit()
    assert _argv(_inv(repo, base, bin_dir))["root:typecheck"] == expected


def test_non_mutating_script_variant_wins(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    scripts = {
        "lint": "biome lint --write .",
        "lint:check": "biome lint .",
        "format:check": "biome check .",
        "typecheck": "tsc --noEmit",
        "pretypecheck": "eslint --fix .",
        "test:typecheck": "tsc -p .",
        "test": "vitest run",
    }
    repo.write("package.json", _package_json(scripts))
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["root:lint"].argv == ("npm", "run", "lint:check")
    assert g["root:lint"].source == "package.json#scripts.lint:check"
    assert g["root:typecheck"].argv == ("npm", "run", "test:typecheck")  # pre-hook mutates


@pytest.mark.parametrize(
    "body", ["eslint --fix .", "prettier --write=true .", "biome check --apply .", "x -w"]
)
def test_only_mutating_script_is_still_a_gate(tmp_path, bin_dir, body):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"lint": body}))
    base = repo.commit()
    lint = _by_id(_inv(repo, base, bin_dir))["root:lint"]
    assert lint.argv == ("npm", "run", "lint")
    assert lint.source == "package.json#scripts.lint (mutating; no check variant)"


def test_m14_script_selection_change_is_a_definition_change(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    scripts = {"lint": "biome lint --write .", "test": "vitest"}
    repo.write("package.json", _package_json(scripts))
    base = repo.commit()
    repo.write("package.json", _package_json({**scripts, "lint:check": "true"}))
    assert _changed(_inv(repo, base, bin_dir)) == ["root:lint"]


def test_test_prefixed_scripts_are_inventoried(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    scripts = {
        "test": 'pnpm run "/^test:.*/"',
        "test:format": "prettier . --list-different",
        "test:types": "tsc --noEmit",
        "test:lint": "eslint .",
    }
    repo.write("package.json", _package_json(scripts))
    repo.write("web/package.json", _package_json({"test:typecheck": "tsc"}))
    base = repo.commit()
    argv = _argv(_inv(repo, base, bin_dir))
    assert argv["root:format"] == ("npm", "run", "test:format")
    assert argv["root:lint"] == ("npm", "run", "test:lint")
    assert argv["root:typecheck"] == ("npm", "run", "test:types")
    assert argv["root:unit"] == ("npm", "run", "test")
    assert argv["web:typecheck"] == ("npm", "run", "test:typecheck")


def test_package_manager_field_selects_the_runner(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"test": "jest"}, packageManager="yarn@4.1.0"))
    repo.write("pnpm-lock.yaml", "")
    repo.write("a/package.json", _package_json({"test": "jest"}))  # inherits the root's field
    repo.write("b/package.json", _package_json({"test": "jest"}, packageManager="bun@1.1.0"))
    repo.write("c/package.json", _package_json({"test": "jest"}, packageManager="pnpm@9+sha1.x"))
    base = repo.commit()
    argv = _argv(_inv(repo, base, bin_dir))
    assert argv["root:unit"][0] == argv["a:unit"][0] == "yarn"
    assert argv["b:unit"][0] == "bun"
    assert argv["c:unit"][0] == "pnpm"


def test_lockfiles_fall_back_to_the_root(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({"test": "jest"}, packageManager="not valid!"))
    repo.write("pnpm-lock.yaml", "")
    repo.write("a/package.json", _package_json({"test": "jest"}))
    repo.write("b/package.json", _package_json({"test": "jest"}))
    repo.write("b/package-lock.json", "{}\n")
    base = repo.commit()
    argv = _argv(_inv(repo, base, bin_dir))
    assert argv["root:unit"][0] == argv["a:unit"][0] == "pnpm"
    assert argv["b:unit"][0] == "npm"


def test_m14_root_package_manager_change_reaches_sub_scopes(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", _package_json({}, packageManager="pnpm@9.0.0"))
    repo.write("a/package.json", _package_json({"test": "jest"}))
    base = repo.commit()
    repo.write("package.json", _package_json({}, packageManager="yarn@4.0.0"))
    assert _changed(_inv(repo, base, bin_dir)) == ["a:unit"]


MAKEFILE = """\
VAR := lint
.PHONY: lint test
lint: ; ruff check --fix .
check:
\truff check .
test typecheck: deps
\tpytest -q
fmt-check:
\tblack --check .
# format-check: not a rule
"""


def test_makefile_targets_are_inventoried(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("Makefile", MAKEFILE)
    repo.write("svc/Makefile", "format-check:\n\tprettier -w .\nlint:\n\teslint .\n")
    repo.write("svc/package.json", _package_json({"lint": "eslint ."}))
    base = repo.commit()
    g = _by_id(_inv(repo, base, bin_dir))
    assert {i: g[i].argv for i in g if i.startswith("root:")} == {
        "root:format": ("make", "fmt-check"),
        "root:lint": ("make", "check"),  # `lint` applies fixes
        "root:typecheck": ("make", "typecheck"),
        "root:unit": ("make", "test"),
    }
    assert g["root:format"].source == "Makefile#fmt-check"
    assert g["root:unit"].cwd == ""
    assert g["svc:lint"].argv == ("npm", "run", "lint")  # package.json precedes the Makefile
    assert g["svc:format"].source == "svc/Makefile#format-check (mutating; no check variant)"


def test_python_config_precedes_makefile(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", '[tool.mypy]\nfiles = ["pkg"]\n[tool.pytest.ini_options]\n')
    repo.write(
        "Makefile", "test:\n\tpytest\nformat-check:\n\tblack --check .\ntypecheck:\n\tmypy\n"
    )
    base = repo.commit()
    argv = _argv(_inv(repo, base, bin_dir))
    assert argv["root:unit"] == ("python", "-m", "pytest", "-q")
    assert argv["root:typecheck"] == ("mypy",)
    assert argv["root:format"] == ("make", "format-check")


def test_m14_makefile_is_a_definition_file(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("Makefile", "test:\n\tpytest\n")
    repo.write("rules.mk", "X = 1\n")
    base = repo.commit()
    repo.write("Makefile", "test:\n\ttrue\n")
    inv = _inv_c(repo, base, bin_dir)
    assert _changed(inv) == ["root:unit"]
    assert _by_id(inv)["root:unit"].source == "Makefile#test; definition changed: Makefile"
    repo.git("checkout", "--", "Makefile")
    repo.write("rules.mk", "X = 2\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == ["root:unit"]
    repo.git("checkout", "--", "rules.mk")
    repo.write("GNUmakefile", "test:\n\ttrue\n")  # make reads it before Makefile
    assert _changed(_inv_c(repo, base, bin_dir)) == ["root:unit"]


def test_m14_source_taking_over_a_gate_is_a_definition_change(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("Makefile", "lint:\n\truff check .\n")
    base = repo.commit()
    repo.write("package.json", _package_json({"lint": "true"}))
    inv = _inv(repo, base, bin_dir)
    assert _changed(inv) == ["root:lint"]
    assert _by_id(inv)["root:lint"].argv == ("make", "lint")  # still the BASE definition


def test_m14_command_running_make_tracks_the_makefile(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("web/package.json", _package_json({"test": "make test"}))
    repo.write("web/Makefile", "test:\n\tjest\n")
    base = repo.commit()
    repo.write("web/Makefile", "test:\n\ttrue\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == ["web:unit"]


def test_pytest_ini_inside_tests_dir_is_not_a_scope(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    repo.write("tests/pytest.ini", "[pytest]\njunit_family=legacy\n")
    repo.write("tests/test_a.py", "")
    base = repo.commit()
    inv = _inv(repo, base, bin_dir)
    assert inv.scopes == ("",)
    assert [g.id for g in inv.gates if g.kind is GateKind.UNIT] == ["root:unit"]
    repo.write("tests/pytest.ini", "[pytest]\naddopts = -k nothing\n")
    assert _changed(_inv_c(repo, base, bin_dir)) == ["root:unit"]


def test_pytest_only_subdirectory_is_a_scope_without_root_pytest(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("pyproject.toml", "[tool.ruff]\n")
    repo.write("svc/pytest.ini", "[pytest]\n")
    repo.write("lib/pytest.ini", "[pytest]\n")
    repo.write("lib/pyproject.toml", "[tool.ruff]\n")
    base = repo.commit()
    assert _inv(repo, base, bin_dir).scopes == ("", "lib/", "svc/")


@pytest.mark.parametrize(
    "declare",
    [
        lambda r: r.write("package.json", json.dumps({"workspaces": ["packages/*", "apps/**"]})),
        lambda r: r.write(
            "package.json", json.dumps({"workspaces": {"packages": ["./packages/*/", "apps/**"]}})
        ),
        lambda r: r.write(
            "pnpm-workspace.yaml",
            "# workspace\npackages:\n  - 'packages/*'\n  - \"apps/**\"  # nested\n"
            "catalog:\n  - ignored/*\n",
        ),
        lambda r: r.write("pnpm-workspace.yaml", "packages: [packages/*, 'apps/**']\n"),
    ],
    ids=["array", "object", "pnpm-block", "pnpm-flow"],
)
def test_workspace_packages_are_scopes(tmp_path, bin_dir, declare):
    repo = GitRepo(tmp_path / "r")
    declare(repo)
    repo.write("packages/zod/package.json", _package_json({"test": "vitest"}))
    repo.write("packages/zod/node_modules/dep/package.json", _package_json({"test": "x"}))
    repo.write("packages/notes/README.md", "# no package.json\n")
    repo.write("apps/web/site/package.json", _package_json({"lint": "eslint ."}))
    repo.write("ignored/x/package.json", _package_json({"test": "x"}))
    repo.write("pnpm-lock.yaml", "")
    base = repo.commit()
    inv = _inv(repo, base, bin_dir)
    assert set(inv.scopes) >= {"apps/web/site/", "packages/zod/"}
    assert not {s for s in inv.scopes if "node_modules" in s or s.startswith("ignored/")}
    g = _by_id(inv)
    assert g["packages/zod:unit"].argv == ("pnpm", "run", "test")
    assert g["packages/zod:unit"].cwd == "packages/zod"
    assert g["apps/web/site:lint"].scope == "apps/web/site/"


def test_workspace_negation_and_immediate_subdirectories(tmp_path, bin_dir):
    repo = GitRepo(tmp_path / "r")
    repo.write("package.json", json.dumps({"workspaces": ["packages/*", "!packages/skip", "web"]}))
    for d in ("packages/a", "packages/skip", "web", "tools"):
        repo.write(f"{d}/package.json", _package_json({"test": "jest"}))
    base = repo.commit()
    assert _inv(repo, base, bin_dir).scopes == ("", "packages/a/", "tools/", "web/")


# ------------------------------------------------------------------- workspace coverage (v0.6)

VITEST_ROOT_CONFIG = 'export default { test: { projects: ["packages/*"] } };\n'


def _monorepo(tmp_path, root_scripts: dict, vitest_config: str | None = VITEST_ROOT_CONFIG):
    repo = GitRepo(tmp_path / "mono")
    repo.write(
        "package.json",
        json.dumps({"workspaces": ["packages/*"], "packageManager": "pnpm@9.1.0",
                    "scripts": root_scripts}),
    )  # fmt: skip
    if vitest_config is not None:
        repo.write("vitest.config.ts", vitest_config)
    repo.write("packages/a/package.json", _package_json({"test": "vitest run", "lint": "eslint ."}))
    repo.write("packages/b/package.json", _package_json({"test": "vitest run --typecheck"}))
    repo.write("tools/package.json", _package_json({"test": "vitest run"}))  # not a workspace
    return repo, repo.commit()


def test_root_vitest_projects_cover_bare_vitest_workspace_packages(tmp_path, bin_dir):
    repo, base = _monorepo(tmp_path, {"test": "vitest run", "lint": "eslint ."})
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["packages/a:unit"].covered_by == "root:unit"
    assert g["packages/a:unit"].coverage_evidence == (
        "root vitest reads vitest.config.ts projects, which include packages/a"
    )
    # extra options: the root run is not the same check
    assert g["packages/b:unit"].covered_by is None
    # not a workspace package: no root runner reaches it
    assert g["tools:unit"].covered_by is None
    # a root `eslint .` is not evidence for the package lint script
    assert g["packages/a:lint"].covered_by is None
    assert g["root:unit"].covered_by is None


def test_no_root_evidence_keeps_package_gates(tmp_path, bin_dir):
    repo, base = _monorepo(tmp_path, {"test": "vitest run"}, vitest_config=None)
    assert all(g.covered_by is None for g in _inv(repo, base, bin_dir).gates)


def test_recursive_root_script_covers_the_same_script_name(tmp_path, bin_dir):
    repo, base = _monorepo(tmp_path, {"lint": "pnpm -r lint", "test": "pnpm -r --filter a test"})
    g = _by_id(_inv(repo, base, bin_dir))
    assert g["packages/a:lint"].covered_by == "root:lint"
    assert "runs 'lint' in every workspace package (pnpm -r lint)" in (
        g["packages/a:lint"].coverage_evidence or ""
    )
    assert g["packages/a:unit"].covered_by is None  # --filter narrows the run


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r.write("vitest.config.ts", "export default { test: {} };\n"),
        lambda r: r.delete("vitest.config.ts"),
        lambda r: r.write(
            "package.json",
            json.dumps({"workspaces": ["packages/b"], "scripts": {"test": "vitest run"}}),
        ),
        lambda r: r.write("packages/a/package.json", _package_json({"test": "vitest run -t x"})),
    ],
    ids=["projects-dropped", "config-deleted", "left-the-workspace", "package-script-changed"],
)
def test_coverage_must_also_hold_in_the_working_tree(tmp_path, bin_dir, edit):
    repo, base = _monorepo(tmp_path, {"test": "vitest run"})
    edit(repo)
    assert _by_id(_inv(repo, base, bin_dir))["packages/a:unit"].covered_by is None
