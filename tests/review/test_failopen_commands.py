"""Fail-open paths in gate execution: gates run code and config the change itself edits."""

from __future__ import annotations

import json
import shutil
import stat

import pytest

from repomarshal import api
from repomarshal.model import Verdict

from .conftest import Webapp, fake_failing_pytest, py_stub, real_python_stub

# (path, content, side): tool configuration that the gate's tool reads but the M14 definition
# model does not cover. Each one can switch a real gate to "exit 0" without touching what M14
# compares.
UNTRACKED_DEFINITIONS = [
    # pytest prefers `.pytest.ini` (pytest >= 7); `--collect-only` exits 0 without running tests.
    ("backend/.pytest.ini", "[pytest]\naddopts = --collect-only\n", "backend"),
    # conftest.py is pytest configuration; this one ends the session with status 0.
    ("backend/tests/conftest.py", "import os\n\nos._exit(0)\n", "backend"),
    # ruff resolves the closest config file per source file; this excludes everything.
    ("backend/app/ruff.toml", 'extend-exclude = ["*"]\n', "backend"),
    # eslint flat config, tsconfig and .npmrc (`script-shell`) are read by the npm scripts.
    ("frontend/eslint.config.js", 'export default [{ ignores: ["**"] }];\n', "frontend"),
    (
        "frontend/tsconfig.json",
        '{"compilerOptions": {"strict": false}, "include": []}\n',
        "frontend",
    ),
    ("frontend/.npmrc", "script-shell=/bin/true\n", "frontend"),
]


@pytest.mark.failopen
@pytest.mark.parametrize(
    ("path", "content", "side"), UNTRACKED_DEFINITIONS, ids=[d[0] for d in UNTRACKED_DEFINITIONS]
)
def test_tool_config_edit_is_a_definition_change(webapp, path, content, side):
    """Editing a config the gate tool reads must count as a gate-definition change (UNKNOWN), as
    editing `[tool.ruff]` or a package.json script already does."""
    w = webapp
    w.repo.write(path, content)
    if side == "backend":
        w.routine_backend_change()
    else:
        w.edit_login_form()
    plan = w.plan()
    changed = sorted(g.id for g in plan.inventory if g.definition_changed)
    res = w.done()
    assert res.verdict != Verdict.PASS, (changed, res.reasons)


@pytest.mark.failopen
def test_npm_script_delegating_to_edited_file(webapp):
    """BASE defines `"test": "node scripts/test.js"`. The agent rewrites scripts/test.js to
    `process.exit(0)`; the script body is unchanged, so M14 trusts the result: PASS."""
    w = webapp
    pkg = json.loads(w.read("frontend/package.json"))
    pkg["scripts"]["test"] = "node scripts/test.js"
    w.commit_on_base("frontend/package.json", json.dumps(pkg, indent=2) + "\n")
    w.commit_on_base("frontend/scripts/test.js", "require('./run-jest')();\n")
    w.repo.write("frontend/scripts/test.js", "process.exit(0);\n")
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


SCRIPT_POLICY = """\
version = 1

[gates.check]
kind = "unit"
scope = "backend/"
argv = ["./scripts/check.sh"]
"""


@pytest.mark.failopen
def test_policy_gate_script_edited_by_change(tmp_path):
    """A policy gate whose argv[0] is a repository script. BASE's script fails; the agent edits
    the script to `exit 0`. The [gates.check] table is unchanged, so the result is trusted."""
    w = Webapp(tmp_path, policy=SCRIPT_POLICY)
    script = w.repo.write("backend/scripts/check.sh", "#!/bin/sh\necho '1 failed'\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    w.base = w.repo.commit("BASE: check script")
    w.routine_backend_change()
    assert w.done().verdict == Verdict.FAIL  # control

    w.repo.write("backend/scripts/check.sh", "#!/bin/sh\nexit 0\n")
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


def _shadow_env(w: Webapp, tmp_path) -> dict[str, str]:
    real_python_stub(w)
    env = w.env
    env.update(fake_failing_pytest(tmp_path))
    return env


def test_unit_suite_failure_is_seen_with_real_interpreter(tmp_path):
    """Guard / control: `python -m pytest` really runs the (failing) suite."""
    w = Webapp(tmp_path)
    env = _shadow_env(w, tmp_path)
    w.routine_backend_change()
    res = api.done(w.root, env=env)
    assert res.verdict == Verdict.FAIL, res.reasons


@pytest.mark.failopen
def test_pytest_module_shadowed_by_file_in_gate_cwd(tmp_path):
    """`python -m pytest` runs with cwd=backend/, and `-m` puts the cwd first on sys.path. The
    agent adds backend/pytest.py (`raise SystemExit(0)`): the real suite is never imported, the
    unit gate exits 0, and the failing change is PASS."""
    w = Webapp(tmp_path)
    env = _shadow_env(w, tmp_path)
    w.routine_backend_change()
    w.repo.write("backend/pytest.py", "raise SystemExit(0)\n")
    res = api.done(w.root, env=env)
    assert res.verdict != Verdict.PASS, res.reasons


@pytest.mark.failopen
def test_tree_swapped_and_restored_between_gates(webapp):
    """Gates run in the live working tree and the digest is only taken before and after. Code
    that runs inside an early gate (eslint.config.js, a test) swaps in a clean file, the later
    typecheck/unit gates verify the clean file, and the last gate restores the agent's file:
    before == after, so the evidence claims a tree that typecheck and unit never saw."""
    w = webapp
    target = w.root / "frontend" / "src" / "components" / "LoginForm.tsx"
    w.append("frontend/src/components/LoginForm.tsx", "\nconst broken: number = 'BAD';\n")
    backup = w.markers / "LoginForm.bak"
    py_stub(
        w.bindir,
        "npm",
        f"""import shutil, sys
target, backup = {str(target)!r}, {str(backup)!r}
script = sys.argv[2]
if script == "lint":
    shutil.copy(target, backup)
    text = open(target).read().replace("\\nconst broken: number = 'BAD';\\n", "")
    open(target, "w").write(text)
elif script in ("typecheck", "test"):
    sys.exit(1 if "BAD" in open(target).read() else 0)
elif script == "test:e2e":
    shutil.copy(backup, target)
""",
    )
    assert shutil.which("npm", path=w.path_env)
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons
