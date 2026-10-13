"""`repomarshal done --hook claude-code` (owner: agent "hosts"; roadmap SHOULD #6; ADR-003).

Every non-PASS path must block the stop: exit 0 with exactly one JSON object
{"decision": "block", "reason": ...} on stdout. PASS allows the stop with empty stdout. (The one
exception, an exact repeat of a recorded block under stop_hook_active, is allowed with an
UNVERIFIED systemMessage; see test_hook_loop.py.) Each case
runs in-process (hooks.run) and as a subprocess (`python -m repomarshal done --hook claude-code`)
against recorded Stop-hook payloads in tests/fixtures/hooks/.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from repomarshal import api, hooks
from tests.acceptance.conftest import Webapp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hooks"
INVALID_POLICY = 'version = 1\n\n[gates.security]\nkind = "security_scan"\nargv = "bandit -r app"\n'
SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture(name="webapp")
def webapp_fixture(tmp_path: Path) -> Webapp:
    """The zero-config webapp acceptance fixture with passing stub tools."""
    return Webapp(tmp_path)


def payload(cwd: Path | str | None, name: str = "claude_code_stop.json") -> str:
    data = json.loads((FIXTURES / name).read_text())
    if cwd is None:
        del data["cwd"]
    else:
        data["cwd"] = str(cwd)
    return json.dumps(data)


def run_inproc(w: Webapp | None, text: str, capsys, monkeypatch, repo: str | None = None) -> tuple:
    if w is not None:
        monkeypatch.setenv("PATH", w.path_env)
    code = hooks.run("claude-code", text, repo=repo)
    out = capsys.readouterr()
    return code, out.out


def run_subproc(w: Webapp, text: str, cwd: Path, *extra: str) -> tuple:
    proc = subprocess.run(
        [sys.executable, "-m", "repomarshal", "done", "--hook", "claude-code", *extra],
        input=text,
        cwd=cwd,
        env=w.cli_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, proc.stdout


def assert_blocks(code: int, stdout: str) -> str:
    assert code == 0, stdout
    lines = stdout.splitlines()
    assert len(lines) == 1, f"stdout must be exactly one JSON object, got {stdout!r}"
    data = json.loads(stdout)  # the whole stdout is one JSON document
    assert isinstance(data, dict)
    assert set(data) == {"decision", "reason"}
    assert data["decision"] == "block"
    reason = data["reason"]
    assert isinstance(reason, str) and reason.strip()
    assert len(reason) <= hooks.MAX_REASON
    return reason


@pytest.fixture(params=["inproc", "subproc"])
def mode(request):
    return request.param


@pytest.fixture
def invoke(mode, capsys, monkeypatch, tmp_path):
    """invoke(webapp, payload_text, repo=None) -> (exit code, stdout) in the current mode."""
    elsewhere = tmp_path / "elsewhere"  # the hook process cwd is never the repository
    elsewhere.mkdir()

    def _invoke(w: Webapp, text: str, repo: str | None = None):
        if mode == "inproc":
            monkeypatch.chdir(elsewhere)
            return run_inproc(w, text, capsys, monkeypatch, repo=repo)
        extra = ("--repo", repo) if repo else ()
        return run_subproc(w, text, elsewhere, *extra)

    return _invoke


# ---------------------------------------------------------------------- verdicts


def test_pass_allows_the_stop(webapp, invoke):
    webapp.scenario_a()
    code, out = invoke(webapp, payload(webapp.root))
    assert code == 0
    assert out.strip() in ("", "{}")


def test_fail_blocks_and_names_the_failing_gate(webapp, invoke):
    webapp.scenario_a_broken_link()
    reason = assert_blocks(*invoke(webapp, payload(webapp.root)))
    assert "FAIL" in reason
    assert "native:docs-links" in reason
    assert "repomarshal plan --explain" in reason
    assert "stop again" in reason


def test_fail_reason_redacts_gate_output(webapp, invoke):
    webapp.scenario_b()
    webapp.stub("npm", exit_code=1, output=f"lint failed: token={SECRET}")
    reason = assert_blocks(*invoke(webapp, payload(webapp.root)))
    assert "frontend:lint" in reason
    assert "lint failed" in reason  # the last output line is shown ...
    assert SECRET not in reason  # ... redacted
    assert "[REDACTED]" in reason


def test_unknown_blocks_zero_config_scenario_c(webapp, invoke):
    webapp.scenario_c()
    reason = assert_blocks(*invoke(webapp, payload(webapp.root)))
    assert "UNKNOWN" in reason
    assert "security_scan" in reason


def test_blocked_invalid_policy_blocks(webapp, invoke):
    webapp.commit_on_base("repomarshal.toml", INVALID_POLICY)
    webapp.scenario_a()
    reason = assert_blocks(*invoke(webapp, payload(webapp.root)))
    assert "BLOCKED" in reason
    assert "repomarshal.toml" in reason


def test_stop_hook_active_still_blocks(webapp, invoke):
    webapp.scenario_a_broken_link()
    reason = assert_blocks(*invoke(webapp, payload(webapp.root, "claude_code_stop_active.json")))
    assert "native:docs-links" in reason
    assert "stop_hook_active" in reason


def test_stop_hook_active_with_pass_allows(webapp, invoke):
    webapp.scenario_a()
    code, out = invoke(webapp, payload(webapp.root, "claude_code_stop_active.json"))
    assert code == 0
    assert out.strip() in ("", "{}")


# ---------------------------------------------------------------------- bad input


@pytest.mark.parametrize("text", ["", "not json", '{"cwd": ', "[1, 2]", "null", "42"])
def test_malformed_payload_blocks(webapp, invoke, text):
    reason = assert_blocks(*invoke(webapp, text))
    assert "BLOCKED" in reason


def test_payload_without_cwd_blocks(webapp, invoke):
    webapp.scenario_a()  # would PASS if the repository were found
    assert_blocks(*invoke(webapp, payload(None)))


def test_missing_cwd_directory_blocks(webapp, invoke, tmp_path):
    reason = assert_blocks(*invoke(webapp, payload(tmp_path / "does-not-exist")))
    assert "does not exist" in reason


def test_non_git_cwd_blocks(webapp, invoke, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    reason = assert_blocks(*invoke(webapp, payload(plain)))
    assert "not inside a git work tree" in reason


def test_wrong_hook_event_blocks(webapp, invoke):
    webapp.scenario_a()
    data = json.loads(payload(webapp.root))
    data["hook_event_name"] = "PreToolUse"
    assert_blocks(*invoke(webapp, json.dumps(data)))


# ---------------------------------------------------------------------- repository selection


def test_explicit_repo_overrides_payload_cwd(webapp, invoke, tmp_path):
    webapp.scenario_a()
    plain = tmp_path / "plain"
    plain.mkdir()
    code, out = invoke(webapp, payload(plain), repo=str(webapp.root))
    assert code == 0
    assert out.strip() in ("", "{}")


def test_default_repo_uses_payload_cwd(webapp, invoke, tmp_path):
    """--repo omitted (CLI default None): the payload cwd decides, not the process cwd."""
    webapp.scenario_a_broken_link()
    reason = assert_blocks(*invoke(webapp, payload(webapp.root)))
    assert "native:docs-links" in reason


# ---------------------------------------------------------------------- crashes


def test_internal_exception_blocks(webapp, capsys, monkeypatch):
    webapp.scenario_a()

    def boom(*args, **kwargs):
        raise RuntimeError(f"kaboom password={SECRET}")

    monkeypatch.setattr(api, "done", boom)
    code, out = run_inproc(webapp, payload(webapp.root), capsys, monkeypatch)
    reason = assert_blocks(code, out)
    assert "internal error" in reason
    assert "RuntimeError" in reason
    assert SECRET not in reason


def test_internal_exception_blocks_in_subprocess(webapp, tmp_path):
    """A crash inside the real CLI process (api.done patched via sitecustomize) still blocks."""
    patch = tmp_path / "patch"
    patch.mkdir()
    (patch / "sitecustomize.py").write_text(
        "import repomarshal.api as api\n"
        "def _boom(*a, **k):\n"
        "    raise RuntimeError('kaboom')\n"
        "api.done = _boom\n"
    )
    env = webapp.cli_env()
    env["PYTHONPATH"] = f"{patch}:{env['PYTHONPATH']}"
    proc = subprocess.run(
        [sys.executable, "-m", "repomarshal", "done", "--hook", "claude-code"],
        input=payload(webapp.root),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    reason = assert_blocks(proc.returncode, proc.stdout)
    assert "kaboom" in reason


def test_system_exit_from_done_blocks(webapp, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise SystemExit(0)  # even a "successful" exit from deep inside must not allow a stop

    monkeypatch.setattr(api, "done", boom)
    code, out = run_inproc(webapp, payload(webapp.root), capsys, monkeypatch)
    assert_blocks(code, out)


def test_stray_prints_do_not_reach_stdout(webapp, capsys, monkeypatch):
    real_done = api.done

    def noisy(*args, **kwargs):
        print("noise on stdout")
        return real_done(*args, **kwargs)

    webapp.scenario_a_broken_link()
    monkeypatch.setattr(api, "done", noisy)
    code, out = run_inproc(webapp, payload(webapp.root), capsys, monkeypatch)
    assert "noise" not in out
    assert_blocks(code, out)


def test_unknown_host_blocks(capsys):
    code = hooks.run("other-host", "{}")
    assert_blocks(code, capsys.readouterr().out)


def test_unwritable_stdout_falls_back_to_exit_2(monkeypatch, capsys):
    class Broken:
        def write(self, _):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    monkeypatch.setattr(sys, "stdout", Broken())
    code = hooks.run("claude-code", "not json")
    assert code == 2
    assert "BLOCKED" in capsys.readouterr().err


def test_reason_is_bounded(monkeypatch, capsys, tmp_path):
    many = tuple(f"gate{i}: UNKNOWN ({'x' * 1000})" for i in range(100))
    fake = api.DoneResult(hooks.Verdict.UNKNOWN, many, None, None, None)
    monkeypatch.setattr(api, "done", lambda *a, **k: fake)
    code = hooks.run("claude-code", payload(tmp_path))
    reason = assert_blocks(code, capsys.readouterr().out)
    assert "- gate0: UNKNOWN" in reason
    more = re.search(r"\.\.\. and (\d+) more", reason)
    assert more and 88 <= int(more.group(1)) < 100
    assert all(len(line) <= hooks.MAX_LINE + 2 for line in reason.splitlines() if line[:2] == "- ")


def test_reason_strips_control_characters(monkeypatch, capsys, tmp_path):
    fake = api.DoneResult(
        hooks.Verdict.FAIL, ("g: FAIL (\x1b[31mred\x1b[0m\x07)",), None, None, None
    )
    monkeypatch.setattr(api, "done", lambda *a, **k: fake)
    code = hooks.run("claude-code", payload(tmp_path))
    reason = assert_blocks(code, capsys.readouterr().out)
    assert "\x1b" not in reason and "\x07" not in reason
    assert "red" in reason


def test_pass_without_a_record_blocks(monkeypatch, capsys, tmp_path):
    fake = api.DoneResult(hooks.Verdict.PASS, ("all passed",), None, None, None)
    monkeypatch.setattr(api, "done", lambda *a, **k: fake)
    code = hooks.run("claude-code", payload(tmp_path))
    reason = assert_blocks(code, capsys.readouterr().out)
    assert "UNKNOWN" in reason


def test_run_restores_signal_handlers_and_cancels_the_deadline(capsys):
    """In-process callers must not inherit the hook's last-resort handlers or its alarm."""
    import signal as _signal

    before = {s: _signal.getsignal(s) for s in (_signal.SIGTERM, _signal.SIGHUP, _signal.SIGALRM)}
    hooks.run("claude-code", "not json")
    capsys.readouterr()
    after = {s: _signal.getsignal(s) for s in before}
    assert after == before
    assert _signal.alarm(0) == 0  # no pending alarm


def test_session_start_anchors_base_so_commits_on_main_are_judged(webapp, capsys, monkeypatch):
    """SessionStart records BASE; a failing change the agent then COMMITS on main (no remote)
    is still judged by the Stop hook instead of disappearing into the automatic BASE."""
    monkeypatch.setenv("PATH", webapp.path_env)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    start = json.dumps({"hook_event_name": "SessionStart", "cwd": str(webapp.root)})
    assert hooks.run("claude-code", start) == 0
    assert capsys.readouterr().out == ""  # never blocks, never writes agent context
    webapp.routine_backend_change()
    webapp.stub("python", exit_code=1, output="1 failed")
    webapp.repo.commit("agent: backend change on main")
    stop = json.dumps({"hook_event_name": "Stop", "cwd": str(webapp.root)})
    code = hooks.run("claude-code", stop)
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)["decision"] == "block", out
