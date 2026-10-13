"""Adversarial review of the v0.2 additions (Stop hook, MCP server, test-tamper signals).

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly.

tests/review/conftest.py registers the marker only for its own directory, so it is registered
here as well.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import GitRepo
from tests.acceptance.conftest import Webapp, webapp  # noqa: F401

HOOK_ARGV = (sys.executable, "-m", "repomarshal", "done", "--hook", "claude-code")
TEST_REFRESH = "backend/tests/test_refresh.py"
LOGIN_TEST = "frontend/src/components/LoginForm.test.tsx"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )


def stop_payload(cwd: Path | str, **extra: object) -> str:
    data: dict[str, object] = {
        "session_id": "00893aaf-19fa-41d2-8238-13269b9b3ca0",
        "transcript_path": "/home/dev/.claude/projects/x/00893aaf.jsonl",
        "cwd": str(cwd),
        "permission_mode": "default",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    data.update(extra)
    return json.dumps(data)


def run_hook(
    w: Webapp, stdin: bytes, env_extra: dict[str, str] | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[bytes]:
    env = w.cli_env()
    env.update(env_extra or {})
    return subprocess.run(
        HOOK_ARGV,
        input=stdin,
        cwd=w.root.parent,
        env=env,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def hook_blocked(code: int, stdout: bytes) -> bool:
    """Whether Claude Code would keep the agent working: exit 2, or exit 0 with a block."""
    if code == 2:
        return True
    if code != 0:
        return False
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("decision") == "block"


def failing_backend(w: Webapp) -> None:
    """A real backend code change whose unit tests fail: the honest verdict is FAIL."""
    w.routine_backend_change()
    w.stub("python", exit_code=1, output="1 failed")


def nested_docs_repo(w: Webapp) -> GitRepo:
    """An agent-created git repository inside the project with a trivial docs-only change."""
    nested = GitRepo(w.root / "vendor" / "notes")
    nested.write("README.md", "# notes\n")
    nested.commit("notes")
    nested.write("README.md", "# notes\n\nSee the [README](README.md).\n")
    return nested
