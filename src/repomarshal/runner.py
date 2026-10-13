"""Gate execution (owner: lead; roadmap M11; architecture §6, §7.6).

Command gates: subprocess with an argv list (never a shell), cwd inside the repository, a timeout,
and a new process group that is killed on timeout. exit 0 -> PASS, non-zero -> FAIL, timeout or
failure to start -> UNKNOWN. Full stdout/stderr are hashed; only the last TAIL_LINES lines are kept,
after redaction. Native gates dispatch to repomarshal.native. A gate whose definition changed in
this change (M14) is UNKNOWN whatever its exit code; the exit code is still recorded.

run_gate never raises: any unexpected error becomes an UNKNOWN result (fail closed).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from repomarshal import native
from repomarshal.model import ChangeSet, Gate, GateResult, ResultStatus, sha256_bytes

TAIL_LINES = 40

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+|basic\s+|token\s+)?\S+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED]"),
    (re.compile(r"\b(sk|rk|pk)[-_](live|test|proj|ant)[-_][A-Za-z0-9_-]{16,}\b"), "[REDACTED]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED JWT]",
    ),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@"), r"\1[REDACTED]@"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|password|passwd|token|access[_-]?key)(\s*[:=]\s*)"
            r"(['\"]?)[^\s'\"]{6,}\3"
        ),
        r"\1\2[REDACTED]",
    ),
)


def redact(text: str) -> str:
    """Replace secrets (tokens, keys, Authorization headers, URL passwords) with [REDACTED]."""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def _tail(data: bytes) -> tuple[str, ...]:
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()[-TAIL_LINES:]
    return tuple(redact(line)[:400] for line in lines)


def run_gate(
    repo: Path, gate: Gate, change: ChangeSet, env: Mapping[str, str] | None = None
) -> GateResult:
    try:
        result = _run(repo, gate, change, env)
    except Exception as e:  # noqa: BLE001 - fail closed: never let a crash look like success
        result = GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"internal error while running gate: {type(e).__name__}: {e}",
            argv=gate.argv,
        )
    if gate.definition_changed and result.status != ResultStatus.UNKNOWN:
        result = GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"gate definition changed in this change ({gate.source}); its result cannot be "
            f"trusted (exit code was {result.exit_code}). Re-verify where the definition is "
            "trusted (after merge, or in CI).",
            argv=result.argv,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            stdout_sha256=result.stdout_sha256,
            stderr_sha256=result.stderr_sha256,
            output_tail=result.output_tail,
        )
    return result


def _run(repo: Path, gate: Gate, change: ChangeSet, env: Mapping[str, str] | None) -> GateResult:
    if gate.provider == "native":
        if gate.id == native.DOCS_LINKS_ID:
            return native.run_docs_links(repo, change)
        if gate.id == native.SECRETS_DIFF_ID:
            return native.run_secrets_diff(change)
        return GateResult(gate.id, gate.kind, ResultStatus.UNKNOWN, "unknown native gate")
    if gate.provider != "command":
        from repomarshal import providers  # named adapters (ADR-002)

        if not gate.available:
            return GateResult(
                gate.id,
                gate.kind,
                ResultStatus.UNKNOWN,
                f"unavailable: {gate.unavailable_reason or 'provider tool not found'}",
            )
        return providers.run(repo, gate, change, env)
    if not gate.available:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"unavailable: {gate.unavailable_reason or 'tool not found'}",
            argv=gate.argv,
        )
    if not gate.argv:
        return GateResult(gate.id, gate.kind, ResultStatus.UNKNOWN, "gate has no command")
    root = Path(repo).resolve()
    cwd = (root / gate.cwd).resolve()
    if not cwd.is_relative_to(root) or not cwd.is_dir():
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"working directory {gate.cwd!r} is missing or outside the repository",
            argv=gate.argv,
        )
    run_env = dict(os.environ if env is None else env)
    # A file the change adds in the gate's cwd (e.g. backend/pytest.py) must not shadow the tool
    # that `python -m <tool>` is meant to run (fail-open review #5).
    run_env["PYTHONSAFEPATH"] = "1"
    start = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False by design (ADR-002)
            list(gate.argv),
            cwd=cwd,
            env=run_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"could not start {gate.argv[0]!r}: {e.strerror or e}",
            argv=gate.argv,
        )
    try:
        out, err = proc.communicate(timeout=gate.timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        timed_out = True
    duration = int((time.monotonic() - start) * 1000)
    common = dict(
        argv=gate.argv,
        duration_ms=duration,
        stdout_sha256=sha256_bytes(out),
        stderr_sha256=sha256_bytes(err),
        output_tail=_tail(err + b"\n" + out),
    )
    if timed_out:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"timed out after {gate.timeout_s}s; result unknown",
            exit_code=None,
            **common,
        )
    code = proc.returncode
    if code == 0:
        return GateResult(
            gate.id, gate.kind, ResultStatus.PASS, "exit code 0", exit_code=0, **common
        )
    if code < 0:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"terminated by signal {-code}; result unknown",
            exit_code=code,
            **common,
        )
    return GateResult(
        gate.id, gate.kind, ResultStatus.FAIL, f"exit code {code}", exit_code=code, **common
    )


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
