"""Shared helpers for provider adapters: subprocess execution, version ranges, evidence tails.

Execution follows runner.py: an argv list (never a shell), a new process group that is killed on
timeout, stdin closed. Nothing here raises for a tool failure; callers map every outcome.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from repomarshal.runner import TAIL_LINES, redact

_VERSION_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)(?:\.(\d+))?")

Version = tuple[int, int, int]


@dataclass(frozen=True)
class Proc:
    """Outcome of one tool invocation."""

    code: int | None  # None when it did not start or timed out
    out: bytes
    err: bytes
    duration_ms: int
    timed_out: bool = False
    start_error: str | None = None


def run_process(argv: list[str], cwd: Path, env: Mapping[str, str], timeout_s: float) -> Proc:
    start = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False by design (ADR-002)
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return Proc(
            None, b"", b"", 0, start_error=f"could not start {argv[0]!r}: {e.strerror or e}"
        )
    try:
        out, err = proc.communicate(timeout=max(timeout_s, 0.001))
        timed_out = False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
        timed_out = True
    duration = int((time.monotonic() - start) * 1000)
    return Proc(None if timed_out else proc.returncode, out, err, duration, timed_out=timed_out)


def which(name: str, env: Mapping[str, str] | None) -> str | None:
    path = (os.environ if env is None else env).get("PATH")
    return shutil.which(name, path=path)


def parse_version(text: object) -> Version | None:
    """First "X.Y[.Z]" in `text` as a tuple, or None."""
    if not isinstance(text, str):
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def version_text(v: Version) -> str:
    return ".".join(str(x) for x in v)


def in_range(v: Version, low: Version, high: Version) -> bool:
    """low <= v < high."""
    return low <= v < high


def tail(*chunks: bytes | str) -> tuple[str, ...]:
    """Last TAIL_LINES lines of the chunks, redacted and length-capped like runner.py."""
    lines: list[str] = []
    for chunk in chunks:
        text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
        lines.extend(text.splitlines())
    return tuple(redact(line)[:400] for line in lines[-TAIL_LINES:])


def first_line(text: object, limit: int = 200) -> str:
    """A short, redacted single line of tool-provided text (for reasons)."""
    if not isinstance(text, str):
        return ""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return redact(line)[:limit]
