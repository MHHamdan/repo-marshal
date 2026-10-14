"""Stop-hook loop termination (v0.3). With stop_hook_active true, an exact repeat of a non-PASS
verdict (same evidence digest) is allowed with an UNVERIFIED systemMessage; nothing else may be
allowed, and no hook run may end without a block decision on a non-PASS verdict (Claude Code
treats a hook that dies, e.g. SIGKILL, as a non-blocking error: the stop goes through).

The state file `.repomarshal/state/hook-blocks.json` is agent-writable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import hooks

SID = "6a1b9a43-0d7e-4c55-9d0c-2f3c1f0e7a10"
SRC = Path(__file__).resolve().parents[2] / "src"


def _unknown_repo(tmp_path: Path) -> GitRepo:
    """A code change in a project with no test tooling: an honest, fast UNKNOWN."""
    r = GitRepo(tmp_path / "repo")
    r.write("README.md", "# demo\n")
    r.commit("BASE")
    r.write("app.py", "def handler():\n    return 1\n")
    return r


def _payload(r: GitRepo, active: bool) -> str:
    return json.dumps(
        {
            "session_id": SID,
            "transcript_path": "/home/dev/.claude/projects/x/t.jsonl",
            "cwd": str(r.root),
            "hook_event_name": "Stop",
            "stop_hook_active": active,
        }
    )


def _hook_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["PYTHONPATH"] = str(SRC)
    env.update(extra)
    return env


def _run_hook(r: GitRepo, active: bool, timeout: float = 120, **extra: str):
    return subprocess.run(
        [sys.executable, "-m", "repomarshal", "done", "--hook", "claude-code"],
        input=_payload(r, active).encode(),
        cwd=r.root.parent,
        env=_hook_env(**extra),
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _blocked(proc: subprocess.CompletedProcess) -> bool:
    if proc.returncode == 2:
        return True
    if proc.returncode != 0:
        return False
    try:
        return json.loads(proc.stdout.decode())["decision"] == "block"
    except (ValueError, KeyError, TypeError):
        return False


# ---------------------------------------------------------------------- confirmed fail-open


@pytest.mark.failopen
def test_state_file_symlinked_to_dev_zero_is_not_read_unbounded(tmp_path):
    """H1. Every non-PASS block calls _Loop.record_block -> _load_state, which does
    `Path.read_text()` on `.repomarshal/state/hook-blocks.json` with no size bound and no
    regular-file check. The agent replaces it with a symlink to /dev/zero: the hook then reads
    until memory runs out. On a host with a memory limit the kernel OOM-kills the hook (SIGKILL,
    no handler possible), the hook prints nothing, and Claude Code treats that as a non-blocking
    error: the stop is ALLOWED on a non-PASS verdict, with no UNVERIFIED message.

    Here the read runs under a 512 MiB address-space limit, so it surfaces as MemoryError instead
    of an OOM kill. Secure behaviour: a non-regular or oversized state file is treated as "no
    previous block" without reading it (bounded read, O_NOFOLLOW / S_ISREG check)."""
    root = tmp_path / "repo"
    state = root / hooks.STATE_FILE
    state.parent.mkdir(parents=True)
    state.symlink_to("/dev/zero")
    probe = (
        "import resource, sys\n"
        "lim = 512 << 20\n"
        "resource.setrlimit(resource.RLIMIT_AS, (lim, lim))\n"
        "from pathlib import Path\n"
        "from repomarshal import hooks\n"
        "print(hooks._load_state(Path(sys.argv[1])))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(root)],
        env=_hook_env(),
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == b"{}", proc.stderr.decode()[-400:]


# ---------------------------------------------------------------------- defended


def test_defended_first_stop_already_active_blocks(tmp_path):
    """Defended: stop_hook_active true on the very first stop (no recorded block) blocks."""
    r = _unknown_repo(tmp_path)
    proc = _run_hook(r, active=True)
    assert _blocked(proc), proc.stdout


def test_defended_repeat_after_a_change_blocks_again(tmp_path):
    """Defended: block (UNKNOWN), the agent edits code (still UNKNOWN), stops again with
    stop_hook_active: a different evidence digest, so it is blocked, not allowed."""
    r = _unknown_repo(tmp_path)
    assert _blocked(_run_hook(r, active=False))
    r.write("app.py", "def handler():\n    return 2\n")
    assert _blocked(_run_hook(r, active=True))
    # an exact repeat now ends the loop, loudly
    proc = _run_hook(r, active=True)
    assert proc.returncode == 0 and "UNVERIFIED" in json.loads(proc.stdout)["systemMessage"]


def test_defended_state_file_is_a_fifo_blocks_at_the_deadline(tmp_path):
    """Defended: a FIFO in place of the state file makes the read hang; the hook's own deadline
    (SIGALRM) still prints a block decision."""
    r = _unknown_repo(tmp_path)
    state = r.root / hooks.STATE_FILE
    state.parent.mkdir(parents=True)
    os.mkfifo(state)
    start = time.monotonic()
    proc = _run_hook(r, active=True, timeout=90, REPOMARSHAL_HOOK_DEADLINE="8")
    assert _blocked(proc), (proc.returncode, proc.stdout)
    assert time.monotonic() - start < 80


def test_defended_forged_entry_for_another_session_does_not_allow(tmp_path):
    """Defended: an entry with the current digest but filed under another session id (e.g.
    copied from a concurrent session) does not allow this session's stop."""
    r = _unknown_repo(tmp_path)
    assert _blocked(_run_hook(r, active=False))
    data = json.loads((r.root / hooks.STATE_FILE).read_text())
    data["sessions"] = {"another-session": data["sessions"][SID]}
    (r.root / hooks.STATE_FILE).write_text(json.dumps(data))
    assert _blocked(_run_hook(r, active=True))
