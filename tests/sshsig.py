"""Test helpers for SSH-signed waiver codes (v0.8, T40): throwaway ed25519 keys made with the
real ssh-keygen, allowed-signers lines and signed reason comments."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from repomarshal import waiversig

needs_ssh_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")

SIGNERS_PATH = ".github/repomarshal_allowed_signers"


@dataclass(frozen=True)
class Key:
    private: Path
    public: str  # "ssh-ed25519 AAAA..."

    def allowed(self, principal: str, options: str = "") -> str:
        opts = f"{options} " if options else ""
        return f"{principal} {opts}{self.public}\n"


def make_key(directory: Path, name: str = "key") -> Key:
    directory.mkdir(parents=True, exist_ok=True)
    priv = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(priv)],
        check=True, capture_output=True,
    )  # fmt: skip
    pub = (directory / f"{name}.pub").read_text().split()
    return Key(priv, f"{pub[0]} {pub[1]}")


def token(key: Key, repo: str, pr: int, head: str, target: str, reason: str) -> str:
    return waiversig.sign(key.private, waiversig.message(repo, pr, head, target, reason))


def signed_body(key: Key, repo: str, pr: int, head: str, target: str, reason: str) -> str:
    """A reason comment as `repomarshal waive-sign` prints it."""
    sig = token(key, repo, pr, head, target, reason)
    return f"repomarshal-waive {target}: {reason}\n{waiversig.SIG_KEYWORD} {target} {sig}\n"


def sign_raw(key: Key, data: bytes, namespace: str) -> str:
    """A signature over arbitrary bytes in any namespace (for forgery tests)."""
    r = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key.private), "-n", namespace],
        input=data, capture_output=True, check=True,
    )  # fmt: skip
    return waiversig.armored_to_token(r.stdout.decode())
