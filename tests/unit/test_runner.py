"""Runner tests (owner: lead): exit codes, timeouts, M14, redaction, fail-closed behaviour."""

import sys
from pathlib import Path

from repomarshal import runner
from repomarshal.model import ChangeSet, Cost, Gate, GateKind, ResultStatus

PY = sys.executable
CHANGE = ChangeSet(base=None, head=None, tree_digest="0" * 40, files=(), uncommitted=False)


def gate(argv, **kw):
    base = dict(
        id="root:unit",
        kind=GateKind.UNIT,
        argv=tuple(argv),
        cwd="",
        scope="",
        cost=Cost.STANDARD,
        source="test",
    )
    base.update(kw)
    return Gate(**base)


def test_exit_zero_is_pass_and_output_hashed(tmp_path: Path):
    r = runner.run_gate(tmp_path, gate([PY, "-c", "print('ok')"]), CHANGE)
    assert r.status is ResultStatus.PASS and r.exit_code == 0
    assert r.stdout_sha256.startswith("sha256:") and "ok" in r.output_tail


def test_nonzero_exit_is_fail(tmp_path: Path):
    r = runner.run_gate(tmp_path, gate([PY, "-c", "raise SystemExit(3)"]), CHANGE)
    assert r.status is ResultStatus.FAIL and r.exit_code == 3


def test_timeout_is_unknown_and_kills_children(tmp_path: Path):
    marker = tmp_path / "late"
    child = tmp_path / "child.py"
    child.write_text(f"import time\ntime.sleep(3)\nopen({str(marker)!r}, 'w').close()\n")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(30)\n"
    )
    r = runner.run_gate(tmp_path, gate([PY, str(parent)], timeout_s=1), CHANGE)
    assert r.status is ResultStatus.UNKNOWN and "timed out" in r.reason
    import time

    time.sleep(3.5)
    assert not marker.exists(), "child process survived the timeout kill"


def test_missing_executable_is_unknown(tmp_path: Path):
    r = runner.run_gate(tmp_path, gate(["definitely-not-a-tool-xyz"]), CHANGE)
    assert r.status is ResultStatus.UNKNOWN


def test_unavailable_gate_is_unknown_without_running(tmp_path: Path):
    marker = tmp_path / "ran"
    g = gate(
        [PY, "-c", f"open({str(marker)!r},'w')"],
        available=False,
        unavailable_reason="tool not found",
    )
    r = runner.run_gate(tmp_path, g, CHANGE)
    assert r.status is ResultStatus.UNKNOWN and not marker.exists()


def test_definition_changed_forces_unknown_even_on_exit_zero(tmp_path: Path):
    g = gate(
        [PY, "-c", "pass"], definition_changed=True, source="frontend/package.json#scripts.test"
    )
    r = runner.run_gate(tmp_path, g, CHANGE)
    assert r.status is ResultStatus.UNKNOWN and r.exit_code == 0
    assert "definition changed" in r.reason


def test_cwd_outside_repo_is_unknown(tmp_path: Path):
    (tmp_path / "repo").mkdir()
    r = runner.run_gate(tmp_path / "repo", gate([PY, "-c", "pass"], cwd="../"), CHANGE)
    assert r.status is ResultStatus.UNKNOWN


def test_killed_by_signal_is_unknown(tmp_path: Path):
    code = "import os,signal; os.kill(os.getpid(), signal.SIGKILL)"
    r = runner.run_gate(tmp_path, gate([PY, "-c", code]), CHANGE)
    assert r.status is ResultStatus.UNKNOWN


def test_unsupported_provider_is_unknown(tmp_path: Path):
    r = runner.run_gate(tmp_path, gate([PY, "-c", "pass"], provider="mystery"), CHANGE)
    assert r.status is ResultStatus.UNKNOWN


def test_redaction():
    secret_lines = [
        "Authorization: Bearer abc.def.ghi",
        "token=ghp_" + "a" * 36,
        "AKIA" + "B" * 16,
        "postgres://user:hunter2pass@db/x",
        'password = "correct-horse-battery"',
    ]
    for line in secret_lines:
        out = runner.redact(line)
        assert "REDACTED" in out, line
    assert "hunter2pass" not in runner.redact(secret_lines[3])
    assert runner.redact("all tests passed") == "all tests passed"


def test_output_tail_is_redacted(tmp_path: Path):
    code = "print('Authorization: Bearer s3cr3tvalue')"
    r = runner.run_gate(tmp_path, gate([PY, "-c", code]), CHANGE)
    assert not any("s3cr3tvalue" in line for line in r.output_tail)
