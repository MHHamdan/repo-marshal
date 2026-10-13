"""Stop-hook block-loop termination (v0.3): allow only an EXACT repeat, and never silently.

A non-PASS stop is allowed only when stop_hook_active is true and the current outcome (verdict +
evidence digest) equals the previous block recorded for the same session. The allowed stop
carries a systemMessage telling the USER the change is UNVERIFIED. Everything else blocks. The
state file is agent-writable, so the forging tests check that editing it can at most produce
that same UNVERIFIED stop, never a silent or PASS-looking one and never on a first stop.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from repomarshal import api, hooks
from repomarshal.model import GateKind, GateResult, ResultStatus
from tests.acceptance.conftest import Webapp

SID = "00893aaf-19fa-41d2-8238-13269b9b3ca0"
OTHER_SID = "11111111-2222-3333-4444-555555555555"
C_LABELS = ("security_scan", "runtime")  # zero-config Scenario C: UNAVAILABLE kinds


@pytest.fixture(name="webapp")
def webapp_fixture(tmp_path: Path, monkeypatch) -> Webapp:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return Webapp(tmp_path)


def stop(w: Webapp, active: object = False, session: object = SID, **extra: object) -> str:
    data: dict[str, object] = {
        "session_id": session,
        "transcript_path": "/home/dev/.claude/projects/x/t.jsonl",
        "cwd": str(w.root),
        "permission_mode": "default",
        "hook_event_name": "Stop",
        "stop_hook_active": active,
    }
    if session is None:
        del data["session_id"]
    data.update(extra)
    return json.dumps(data)


@pytest.fixture(params=["inproc", "subproc"])
def mode(request):
    return request.param


@pytest.fixture
def invoke(mode, capsys, monkeypatch, tmp_path):
    """invoke(webapp, payload_text) -> (exit code, stdout) in the current mode."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def _invoke(w: Webapp, text: str):
        if mode == "inproc":
            monkeypatch.chdir(elsewhere)
            monkeypatch.setenv("PATH", w.path_env)
            code = hooks.run("claude-code", text)
            return code, capsys.readouterr().out
        proc = subprocess.run(
            [sys.executable, "-m", "repomarshal", "done", "--hook", "claude-code"],
            input=text,
            cwd=elsewhere,
            env=w.cli_env(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return proc.returncode, proc.stdout

    return _invoke


@pytest.fixture
def inproc(capsys, monkeypatch):
    def _invoke(w: Webapp, text: str):
        monkeypatch.setenv("PATH", w.path_env)
        code = hooks.run("claude-code", text)
        return code, capsys.readouterr().out

    return _invoke


# ---------------------------------------------------------------------- classification


def blocks(code: int, out: str) -> str:
    assert code == 0, out
    assert len(out.splitlines()) == 1, out
    data = json.loads(out)
    assert set(data) == {"decision", "reason"}, data
    assert data["decision"] == "block"
    return data["reason"]


def allowed_unverified(code: int, out: str) -> str:
    """An allowed non-PASS stop: exactly one {"systemMessage": ...} with no decision, exit 0."""
    assert code == 0, out
    assert len(out.splitlines()) == 1, out
    data = json.loads(out)
    assert set(data) == {"systemMessage"}, data
    msg = data["systemMessage"]
    assert "UNVERIFIED" in msg
    assert "repomarshal plan --explain" in msg
    assert len(msg) <= hooks.MAX_MESSAGE
    return msg


def never_silent(code: int, out: str) -> None:
    """Whatever the state says, a non-PASS stop is either blocked or loudly UNVERIFIED."""
    if code == 2:
        return
    assert code == 0 and out.strip(), (code, out)
    data = json.loads(out)
    assert data.get("decision") == "block" or "UNVERIFIED" in data.get("systemMessage", ""), data
    assert "verdict PASS" not in str(data.get("systemMessage", ""))


def state_path(w: Webapp) -> Path:
    return w.root / hooks.STATE_FILE


def read_state(w: Webapp) -> dict:
    return json.loads(state_path(w).read_text())


def write_state(w: Webapp, content: object) -> None:
    p = state_path(w)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if isinstance(content, str) else json.dumps(content))


def entry(verdict: str, digest: str, count: object = 1, event: str = "Stop") -> dict:
    return {
        "event": event,
        "verdict": verdict,
        "digest": digest,
        "count": count,
        "updated_at": "2026-01-01T00:00:00Z",
    }


def forged(w: Webapp, e: dict, session: str = SID) -> None:
    write_state(w, {"version": hooks.STATE_VERSION, "sessions": {session: e}})


def current(w: Webapp) -> tuple[str, str]:
    """(verdict, evidence digest) that the hook will compute for the current tree."""
    r = w.done()
    assert r.record is not None
    return r.verdict.value, hooks.evidence_digest(r.record)


def failing_backend(w: Webapp) -> None:
    w.routine_backend_change()
    w.stub("python", exit_code=1, output="1 failed")


# ---------------------------------------------------------------------- loop termination


def test_exact_repeat_is_allowed_with_unverified_message(webapp, invoke):
    webapp.scenario_c()
    reason = blocks(*invoke(webapp, stop(webapp)))
    assert "UNKNOWN" in reason
    st = read_state(webapp)["sessions"][SID]
    assert st["verdict"] == "UNKNOWN" and st["count"] == 1 and st["event"] == "Stop"

    msg = allowed_unverified(*invoke(webapp, stop(webapp, active=True)))
    assert "verdict UNKNOWN" in msg
    for label in C_LABELS:
        assert label in msg
    assert "required but unavailable" in msg  # one-line reasons


def test_repeated_fail_is_allowed_only_unverified_and_names_the_gate(webapp, invoke):
    failing_backend(webapp)
    blocks(*invoke(webapp, stop(webapp)))
    msg = allowed_unverified(*invoke(webapp, stop(webapp, active=True)))
    assert "verdict FAIL" in msg
    assert "backend:unit" in msg


def test_first_stop_blocks_even_if_active(webapp, inproc):
    webapp.scenario_c()
    reason = blocks(*inproc(webapp, stop(webapp, active=True)))
    assert "stop_hook_active" in reason
    allowed_unverified(*inproc(webapp, stop(webapp, active=True)))  # now it is a repeat


def test_allow_starts_a_new_cycle(webapp, inproc):
    webapp.scenario_c()
    blocks(*inproc(webapp, stop(webapp)))
    allowed_unverified(*inproc(webapp, stop(webapp, active=True)))
    assert SID not in read_state(webapp)["sessions"]
    blocks(*inproc(webapp, stop(webapp, active=True)))  # the next stop blocks again first
    allowed_unverified(*inproc(webapp, stop(webapp, active=True)))


def test_count_grows_with_identical_blocks(webapp, inproc):
    webapp.scenario_c()
    for n in (1, 2, 3):
        blocks(*inproc(webapp, stop(webapp)))
        assert read_state(webapp)["sessions"][SID]["count"] == n
    msg = allowed_unverified(*inproc(webapp, stop(webapp, active=True)))
    assert "after 3 identical" in msg


# ---------------------------------------------------------------------- anything else blocks


def test_other_session_blocks(webapp, invoke):
    webapp.scenario_c()
    blocks(*invoke(webapp, stop(webapp, session=SID)))
    blocks(*invoke(webapp, stop(webapp, active=True, session=OTHER_SID)))


@pytest.mark.parametrize("session", [None, "", "   ", 7, ["x"]])
def test_missing_or_bad_session_id_never_allows(webapp, inproc, session):
    webapp.scenario_c()
    blocks(*inproc(webapp, stop(webapp, session=session)))
    blocks(*inproc(webapp, stop(webapp, active=True, session=session)))


def test_subagent_stop_does_not_reuse_a_stop_block(webapp, inproc):
    webapp.scenario_c()
    blocks(*inproc(webapp, stop(webapp)))
    blocks(*inproc(webapp, stop(webapp, active=True, hook_event_name="SubagentStop")))


def test_changed_code_blocks_and_restarts_the_count(webapp, invoke):
    webapp.scenario_c()
    blocks(*invoke(webapp, stop(webapp)))
    webapp.append("backend/app/auth/refresh.py", "\n# the agent tried something\n")
    blocks(*invoke(webapp, stop(webapp, active=True)))
    assert read_state(webapp)["sessions"][SID]["count"] == 1
    allowed_unverified(*invoke(webapp, stop(webapp, active=True)))


def test_worsened_unknown_then_fail_blocks(webapp, invoke):
    webapp.scenario_c()
    blocks(*invoke(webapp, stop(webapp)))
    webapp.stub("python", exit_code=1, output="1 failed")  # same tree, now FAIL
    reason = blocks(*invoke(webapp, stop(webapp, active=True)))
    assert "FAIL" in reason
    st = read_state(webapp)["sessions"][SID]
    assert st["verdict"] == "FAIL" and st["count"] == 1


def test_new_gates_in_the_plan_block(webapp, inproc):
    webapp.scenario_c()
    blocks(*inproc(webapp, stop(webapp)))
    webapp.edit_login_form()  # the change now also needs the frontend gates
    reason = blocks(*inproc(webapp, stop(webapp, active=True)))
    assert "UNKNOWN" in reason


def _fake_done(monkeypatch, result: api.DoneResult) -> None:
    monkeypatch.setattr(api, "done", lambda *a, **k: result)


def test_different_gate_set_with_same_tree_and_plan_blocks(webapp, inproc, monkeypatch):
    webapp.scenario_c()
    real = webapp.done()
    _fake_done(monkeypatch, real)
    blocks(*inproc(webapp, stop(webapp)))

    extra = GateResult("backend:extra", GateKind.UNIT, ResultStatus.PASS, "exit code 0")
    more = dataclasses.replace(real.record, results=(*real.record.results, extra))
    _fake_done(monkeypatch, dataclasses.replace(real, record=more))
    blocks(*inproc(webapp, stop(webapp, active=True)))


def test_changed_gate_status_with_same_tree_and_plan_blocks(webapp, inproc, monkeypatch):
    webapp.scenario_c()
    real = webapp.done()
    _fake_done(monkeypatch, real)
    blocks(*inproc(webapp, stop(webapp)))

    first, *rest = real.record.results
    flipped = dataclasses.replace(first, status=ResultStatus.UNKNOWN, reason="flaky")
    record = dataclasses.replace(real.record, results=(flipped, *rest))
    _fake_done(monkeypatch, dataclasses.replace(real, record=record))
    blocks(*inproc(webapp, stop(webapp, active=True)))


def test_different_unverified_kinds_block(webapp, inproc, monkeypatch):
    webapp.scenario_c()
    real = webapp.done()
    _fake_done(monkeypatch, real)
    blocks(*inproc(webapp, stop(webapp)))

    reasons = real.record.verdict_reasons[:1]
    record = dataclasses.replace(real.record, verdict_reasons=reasons)
    _fake_done(monkeypatch, dataclasses.replace(real, reasons=reasons, record=record))
    blocks(*inproc(webapp, stop(webapp, active=True)))


def test_digest_ignores_created_at(webapp):
    webapp.scenario_c()
    r = webapp.done()
    later = dataclasses.replace(r.record, created_at="2099-01-01T00:00:00Z").with_digest()
    assert later.record_digest != r.record.record_digest
    assert hooks.evidence_digest(later) == hooks.evidence_digest(r.record)


@pytest.mark.parametrize("active", [False, None, "true", 1, "True"])
def test_stop_hook_active_not_true_blocks_whatever_the_state(webapp, inproc, active):
    webapp.scenario_c()
    verdict, digest = current(webapp)
    forged(webapp, entry(verdict, digest, count=99))
    blocks(*inproc(webapp, stop(webapp, active=active)))


def test_record_less_outcome_never_allows(webapp, inproc):
    """BLOCKED before a plan (invalid policy at BASE) has no evidence record and so no digest:
    it blocks on every stop. The user interrupts, or the policy is fixed on the base branch."""
    webapp.commit_on_base(
        "repomarshal.toml", 'version = 1\n\n[gates.s]\nkind = "security_scan"\nargv = "x"\n'
    )
    webapp.scenario_a()
    for active in (False, True, True):
        assert "BLOCKED" in blocks(*inproc(webapp, stop(webapp, active=active)))


def test_pass_without_record_never_allows(webapp, inproc, monkeypatch):
    _fake_done(monkeypatch, api.DoneResult(hooks.Verdict.PASS, ("ok",), None, None, None))
    for active in (False, True, True):
        blocks(*inproc(webapp, stop(webapp, active=active)))


def test_malformed_payload_blocks_even_with_matching_state(webapp, inproc):
    webapp.scenario_c()
    verdict, digest = current(webapp)
    forged(webapp, entry(verdict, digest, count=5))
    for text in ("", "not json", "[]", '{"stop_hook_active": true}'):
        blocks(*inproc(webapp, text))


def test_errors_still_block_on_a_repeat(webapp, inproc, monkeypatch):
    webapp.scenario_c()
    blocks(*inproc(webapp, stop(webapp)))

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api, "done", boom)
    assert "internal error" in blocks(*inproc(webapp, stop(webapp, active=True)))


def test_unwritable_stdout_on_allow_blocks_via_exit_2(webapp, capsys, monkeypatch):
    webapp.scenario_c()
    monkeypatch.setenv("PATH", webapp.path_env)
    hooks.run("claude-code", stop(webapp))
    capsys.readouterr()

    class Broken:
        def write(self, _):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    monkeypatch.setattr(sys, "stdout", Broken())
    assert hooks.run("claude-code", stop(webapp, active=True)) == 2
    assert "UNVERIFIED" in capsys.readouterr().err


# ---------------------------------------------------------------------- forged state


def test_forged_matching_entry_never_allows_the_first_stop(webapp, invoke):
    webapp.scenario_c()
    verdict, digest = current(webapp)
    forged(webapp, entry(verdict, digest, count=50))
    blocks(*invoke(webapp, stop(webapp)))
    # The worst a forger gets: the same UNVERIFIED stop an unforged repeat gets, with real gates.
    msg = allowed_unverified(*invoke(webapp, stop(webapp, active=True)))
    for label in C_LABELS:
        assert label in msg


def test_forged_entry_on_an_active_stop_is_loud_and_shows_the_real_gates(webapp, invoke):
    """Even skipping the first block (forged entry + an active stop) ends UNVERIFIED, with the
    gates of the CURRENT run, never silently."""
    failing_backend(webapp)
    verdict, digest = current(webapp)
    forged(webapp, entry(verdict, digest))
    code, out = invoke(webapp, stop(webapp, active=True))
    never_silent(code, out)
    msg = allowed_unverified(code, out)
    assert "verdict FAIL" in msg and "backend:unit" in msg


def test_forged_unknown_entry_does_not_let_a_fail_through(webapp, inproc):
    failing_backend(webapp)
    _, digest = current(webapp)
    blocks(*inproc(webapp, stop(webapp)))
    forged(webapp, entry("UNKNOWN", digest, count=9))
    blocks(*inproc(webapp, stop(webapp, active=True)))


@pytest.mark.parametrize("claimed", ["PASS", "BLOCKED", "UNKNOWN", "pass", ""])
def test_edited_verdict_blocks_a_fail(webapp, inproc, claimed):
    failing_backend(webapp)
    blocks(*inproc(webapp, stop(webapp)))
    st = read_state(webapp)
    st["sessions"][SID]["verdict"] = claimed
    write_state(webapp, st)
    blocks(*inproc(webapp, stop(webapp, active=True)))


@pytest.mark.parametrize("digest", ["0" * 64, "A" * 64, "x", "", None, 5])
def test_edited_digest_blocks(webapp, inproc, digest):
    webapp.scenario_c()
    blocks(*inproc(webapp, stop(webapp)))
    st = read_state(webapp)
    st["sessions"][SID]["digest"] = digest
    write_state(webapp, st)
    blocks(*inproc(webapp, stop(webapp, active=True)))


@pytest.mark.parametrize("count", [0, -1, True, "5", 1.5, None])
def test_invalid_count_blocks(webapp, inproc, count):
    webapp.scenario_c()
    verdict, digest = current(webapp)
    forged(webapp, entry(verdict, digest, count=count))
    blocks(*inproc(webapp, stop(webapp, active=True)))


def test_entry_for_another_event_blocks(webapp, inproc):
    webapp.scenario_c()
    verdict, digest = current(webapp)
    forged(webapp, entry(verdict, digest, event="SubagentStop"))
    blocks(*inproc(webapp, stop(webapp, active=True)))


@pytest.mark.parametrize(
    "content",
    [
        "garbage{{{",
        "",
        "\x00\x01",
        "[]",
        "null",
        '{"version": 1, "sessions": []}',
        '{"version": 2, "sessions": {}}',
        '{"sessions": {}}',
    ],
    ids=["garbage", "empty", "binary", "list", "null", "sessions-list", "version", "no-version"],
)
def test_corrupt_state_blocks_and_is_rewritten(webapp, invoke, content):
    webapp.scenario_c()
    write_state(webapp, content)
    blocks(*invoke(webapp, stop(webapp, active=True)))
    assert read_state(webapp)["sessions"][SID]["count"] == 1  # replaced by a real entry


def test_wrong_version_with_matching_entry_blocks(webapp, inproc):
    webapp.scenario_c()
    verdict, digest = current(webapp)
    write_state(webapp, {"version": 2, "sessions": {SID: entry(verdict, digest)}})
    blocks(*inproc(webapp, stop(webapp, active=True)))


def test_unreadable_state_blocks(webapp, inproc):
    webapp.scenario_c()
    state_path(webapp).mkdir(parents=True)  # a directory where the file should be
    blocks(*inproc(webapp, stop(webapp)))
    blocks(*inproc(webapp, stop(webapp, active=True)))


def test_state_writes_do_not_change_the_digest(webapp, inproc):
    """The state file lives under .repomarshal/, which the tree digest excludes."""
    webapp.scenario_c()
    before = current(webapp)
    blocks(*inproc(webapp, stop(webapp)))
    assert current(webapp) == before


def test_state_is_bounded(webapp, inproc):
    webapp.scenario_c()
    sessions = {
        f"s{i}": entry("UNKNOWN", "0" * 64) | {"updated_at": f"2026-01-01T00:00:{i % 60:02d}Z"}
        for i in range(hooks.MAX_SESSIONS + 20)
    }
    write_state(webapp, {"version": hooks.STATE_VERSION, "sessions": sessions})
    blocks(*inproc(webapp, stop(webapp)))
    kept = read_state(webapp)["sessions"]
    assert len(kept) == hooks.MAX_SESSIONS
    assert SID in kept


# ---------------------------------------------------------------------- PASS


@pytest.mark.parametrize("active", [False, True])
def test_pass_still_allows_silently(webapp, invoke, active):
    webapp.scenario_a()
    forged(webapp, entry("FAIL", "0" * 64, count=3))
    code, out = invoke(webapp, stop(webapp, active=active))
    assert code == 0
    assert out == ""
