"""Shared test helpers (owner: lead; part of the contract). Agents may use, not modify, them."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": os.environ.get("HOME", "/tmp"),
    "PATH": os.environ.get("PATH", ""),
}


class GitRepo:
    """A throwaway git repository for tests. Commits use a fixed fixture identity, never the
    project owner's identity, and the repo never has a remote unless a test adds one."""

    def __init__(self, root: Path, branch: str = "main"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", branch)
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str, check: bool = True) -> str:
        r = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            env=GIT_ENV,
            check=False,
        )
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
        return r.stdout.strip()

    def write(self, path: str, content: str | bytes) -> Path:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
        return p

    def delete(self, path: str) -> None:
        (self.root / path).unlink()

    def commit(self, message: str = "commit", add_all: bool = True) -> str:
        if add_all:
            self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def stub_bin(directory: Path, name: str, exit_code: int = 0, output: str = "") -> Path:
    """Create an executable stub `name` in `directory` that prints `output` and exits."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(f"#!/bin/sh\nprintf '%s\\n' {_sh_quote(output)}\nexit {exit_code}\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def env_with_path(*dirs: Path, keep_system: bool = True) -> dict[str, str]:
    """os.environ copy whose PATH starts with `dirs` (and keeps git/python reachable)."""
    env = dict(os.environ)
    parts = [str(d) for d in dirs]
    if keep_system:
        parts.append(os.environ.get("PATH", ""))
    else:
        # keep only directories containing git and python3 so the core still works
        import shutil

        for tool in ("git", "python3"):
            found = shutil.which(tool)
            if found:
                parts.append(str(Path(found).parent))
    env["PATH"] = os.pathsep.join(parts)
    return env


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
