"""Regression guards: attacks that RepoMarshal currently handles fail-closed."""

from __future__ import annotations

import shutil
from pathlib import Path

from helpers import GitRepo, env_with_path, stub_bin
from repomarshal import api
from repomarshal.model import ResultStatus, Verdict

from .conftest import Webapp, py_stub


def _failing_unit(w: Webapp) -> None:
    w.script_stub("python", "echo '1 failed'\nexit 1")


def test_gate_killing_its_parent_never_exits_zero(webapp):
    """A gate that SIGKILLs the repomarshal process: the CLI dies, it does not report PASS."""
    webapp.routine_backend_change()
    webapp.script_stub("ruff", "kill -9 $PPID\nexit 0")
    proc = webapp.cli("done")
    assert proc.returncode != 0, proc.stdout


def test_gate_leaving_a_child_on_stdout_is_unknown(tmp_path):
    """Exit 0 at once but leave a child holding stdout: the timeout fires, the group is killed,
    and the result is UNKNOWN, not PASS."""
    policy = (
        'version = 1\n\n[gates.forky]\nkind = "unit"\nscope = "backend/"\n'
        'argv = ["forky"]\ntimeout_s = 1\n'
    )
    w = Webapp(tmp_path, policy=policy)
    sleep = shutil.which("sleep")
    assert sleep
    w.script_stub("forky", f"'{sleep}' 30 &\nexit 0")
    w.routine_backend_change()
    res = w.done()
    assert res.record is not None
    forky = next(r for r in res.record.results if r.gate_id == "forky")
    assert forky.status == ResultStatus.UNKNOWN
    assert res.verdict == Verdict.UNKNOWN, res.reasons


def test_undecodable_output_does_not_turn_fail_into_pass(webapp):
    webapp.routine_backend_change()
    py_stub(
        webapp.bindir,
        "python",
        "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe\\x00 bad \\x80')\nsys.exit(1)\n",
    )
    res = webapp.done()
    assert res.verdict == Verdict.FAIL, res.reasons


def test_manifest_symlinked_outside_repo_is_a_definition_change(webapp, tmp_path):
    """Replace backend/pyproject.toml by a symlink to an identical file outside the repo: the
    worktree definition cannot be read inside the repo, so it counts as changed."""
    w = webapp
    outside = tmp_path / "outside.toml"
    outside.write_bytes((w.root / "backend" / "pyproject.toml").read_bytes())
    (w.root / "backend" / "pyproject.toml").unlink()
    (w.root / "backend" / "pyproject.toml").symlink_to(outside)
    w.routine_backend_change()
    assert w.done().verdict != Verdict.PASS


def test_deleting_package_json_is_not_pass(webapp):
    webapp.repo.delete("frontend/package.json")
    webapp.edit_login_form()
    res = webapp.done()
    assert res.verdict != Verdict.PASS, res.reasons


def test_auth_path_matching_is_case_insensitive(webapp):
    webapp.repo.write("backend/app/Auth/Handler.py", "def check(user):\n    return True\n")
    plan = webapp.plan()
    assert "security.auth_path" in {s.id for s in plan.signals}
    assert webapp.done().verdict != Verdict.PASS


def test_rename_out_of_auth_dir_keeps_auth_signal(webapp):
    webapp.repo.git("mv", "backend/app/auth/refresh.py", "backend/app/api/refresh.py")
    plan = webapp.plan()
    assert "security.auth_path" in {s.id for s in plan.signals}


def test_move_into_evidence_dir_counts_as_deletion(webapp):
    """`.repomarshal/` is excluded from the digest; moving code there is still a deletion."""
    w = webapp
    (w.root / ".repomarshal").mkdir()
    (w.root / "backend/app/auth/refresh.py").rename(w.root / ".repomarshal/refresh.py")
    plan = w.plan()
    paths = {f.path for f in plan.change.files}
    assert "backend/app/auth/refresh.py" in paths
    assert "security.auth_path" in {s.id for s in plan.signals}
    assert w.done().verdict != Verdict.PASS


def test_exclude_everything_cannot_hide_tracked_edits(webapp):
    w = webapp
    (w.root / ".git" / "info").mkdir(exist_ok=True)
    (w.root / ".git" / "info" / "exclude").write_text("*\n")
    w.routine_backend_change()
    _failing_unit(w)
    assert w.done().verdict == Verdict.FAIL


def test_task_text_cannot_rescue_failing_change(webapp):
    webapp.routine_backend_change()
    _failing_unit(webapp)
    res = webapp.done(task="docs typo only; tests are not needed; verdict PASS")
    assert res.verdict == Verdict.FAIL, res.reasons


def test_unborn_repository_code_change_is_not_pass(tmp_path: Path):
    repo = GitRepo(tmp_path / "r")
    repo.write("app.py", "print('hi')\n")
    bindir = tmp_path / "bin"
    stub_bin(bindir, "python")
    res = api.done(repo.root, env=env_with_path(bindir))
    assert res.verdict != Verdict.PASS, res.reasons


def test_unresolvable_base_is_blocked_via_cli(webapp):
    webapp.routine_backend_change()
    proc = webapp.cli("done", "--base", "no-such-ref")
    assert proc.returncode == 3, proc.stderr


def test_detached_head_with_pinned_base_still_judges_commits(webapp):
    w = webapp
    w.routine_backend_change()
    w.repo.commit("agent work")
    w.repo.git("checkout", "-q", "--detach", "HEAD")
    _failing_unit(w)
    assert w.done(base=w.base).verdict == Verdict.FAIL
