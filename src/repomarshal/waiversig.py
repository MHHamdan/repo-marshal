"""SSH-signed waiver codes (v0.8, threat T40).

A local coding agent that runs as the maintainer (their `gh` login or personal access token)
makes label and comment events with `performed_via_github_app: null`: to the GitHub API it IS the
maintainer. When the BASE policy names an allowed-signers file (`completion.waiver_signers`), a
label waiver therefore also needs a signature made with the maintainer's SSH signing key, posted
in the same reason comment:

    repomarshal-waive <target>: <reason>
    repomarshal-waive-sig <target> <base64 SSHSIG blob>

The signature covers `message(...)`: the repository, PR number, PR head SHA, target and reason,
so it cannot be replayed on another PR, another push, another target or another reason. The
judge checks it with `ssh-keygen -Y verify` against the allowed-signers file read from BASE, with
the GitHub login of the comment author as the principal.

The protection is exactly as strong as the key's. A software key loaded in ssh-agent can be used
by any process running as the maintainer, an agent included. A FIDO hardware key
(`ssh-keygen -t ed25519-sk`) needs a physical touch for every signature, and `ssh-keygen -Y
verify` rejects an sk signature made without user presence unless the allowed-signers line says
`no-touch-required`, which then makes a waiver a human act.

    message(repository, pr, head, target, reason) -> bytes
    sign(key_path, msg) -> str                         (base64, via ssh-keygen -Y sign)
    verify(allowed_signers, principal, msg, sig) -> (bool, why)
    cli_sign(args) -> int                              (`repomarshal waive-sign`)
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NAMESPACE = "repomarshal-waiver"
SCHEMA = "repomarshal-waiver/1"
SIG_KEYWORD = "repomarshal-waive-sig"

MAX_SIG_CHARS = 8192
MAX_SIGNERS_BYTES = 64 * 1024
TIMEOUT_S = 15.0

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_B64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")


class SignatureError(Exception):
    pass


def message(repository: str, pr: int, head: str, target: str, reason: str) -> bytes:
    """The exact bytes that are signed. Every field is single-line and validated, so two different
    waivers can never produce the same message."""
    if not _REPO_RE.fullmatch(repository):
        raise SignatureError(f"repository {repository!r} is not OWNER/REPO")
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise SignatureError(f"pull request number {pr!r} is not a positive integer")
    if not _SHA_RE.fullmatch(head):
        raise SignatureError(f"head {head!r} is not a full lower-case commit SHA")
    for name, value in (("target", target), ("reason", reason)):
        if not value or value != value.strip() or any(c in value for c in "\r\n\0"):
            raise SignatureError(f"{name} must be one non-empty line without outer spaces")
    text = (
        f"{SCHEMA}\nrepository: {repository}\npr: {pr}\nhead: {head}\n"
        f"target: {target}\nreason: {reason}\n"
    )
    return text.encode("utf-8")


def _ssh_keygen() -> str:
    exe = shutil.which("ssh-keygen")
    if not exe:
        raise SignatureError("ssh-keygen is not installed (OpenSSH 8.2 or later is needed)")
    return exe


def _env(home: str) -> dict[str, str]:
    # No agent socket, no user config: verification depends only on the files given.
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LC_ALL": "C"}


def sign(key_path: str | Path, msg: bytes) -> str:
    """Sign `msg` with the private key at `key_path` (or the private half of a .pub file held by
    ssh-agent or a hardware key). Returns the signature as one base64 token. ssh-keygen prompts for
    a passphrase or a touch on the terminal as needed."""
    key = Path(key_path).expanduser()
    if not key.exists():
        raise SignatureError(f"no key file at {key}")
    env = {**os.environ, "LC_ALL": "C"}  # keep SSH_AUTH_SOCK: the key may live in the agent
    r = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [_ssh_keygen(), "-Y", "sign", "-f", str(key), "-n", NAMESPACE],
        input=msg,
        capture_output=True,
        timeout=300,  # a person may have to find and touch the key
        check=False,
        env=env,
    )
    if r.returncode != 0:
        why = r.stderr.decode("utf-8", "replace").strip()
        raise SignatureError(f"ssh-keygen -Y sign failed: {why}")
    return armored_to_token(r.stdout.decode("ascii", "replace"))


def armored_to_token(armored: str) -> str:
    lines = [ln.strip() for ln in armored.strip().splitlines()]
    if (
        len(lines) < 3
        or lines[0] != "-----BEGIN SSH SIGNATURE-----"
        or lines[-1] != "-----END SSH SIGNATURE-----"
    ):
        raise SignatureError("ssh-keygen did not return an SSH signature")
    return "".join(lines[1:-1])


def _token_to_armored(token: str) -> str:
    if len(token) > MAX_SIG_CHARS or not _B64_RE.fullmatch(token):
        raise SignatureError("signature is not one base64 token")
    try:
        blob = base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError) as e:
        raise SignatureError("signature is not valid base64") from e
    if not blob.startswith(b"SSHSIG"):
        raise SignatureError("signature is not an SSHSIG blob")
    body = base64.b64encode(blob).decode("ascii")
    wrapped = "\n".join(body[i : i + 70] for i in range(0, len(body), 70))
    return f"-----BEGIN SSH SIGNATURE-----\n{wrapped}\n-----END SSH SIGNATURE-----\n"


def verify(allowed_signers: bytes, principal: str, msg: bytes, token: str) -> tuple[bool, str]:
    """(valid, why). Valid only when ssh-keygen confirms that `token` signs `msg` in the waiver
    namespace with a key the allowed-signers file lists for `principal`. Never raises."""
    try:
        if not _PRINCIPAL_RE.fullmatch(principal):
            return False, f"principal {principal!r} is not a GitHub login"
        if not allowed_signers.strip():
            return False, "the allowed-signers file is empty"
        if len(allowed_signers) > MAX_SIGNERS_BYTES:
            return False, "the allowed-signers file is too large"
        armored = _token_to_armored(token)
        exe = _ssh_keygen()
        with tempfile.TemporaryDirectory(prefix="repomarshal-sig-") as tmp:
            signers = Path(tmp) / "allowed_signers"
            sig = Path(tmp) / "waiver.sig"
            signers.write_bytes(allowed_signers)
            sig.write_text(armored, encoding="ascii")
            r = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [exe, "-Y", "verify", "-f", str(signers), "-I", principal,
                 "-n", NAMESPACE, "-s", str(sig)],
                input=msg, capture_output=True, timeout=TIMEOUT_S, check=False, env=_env(tmp),
            )  # fmt: skip
    except SignatureError as e:
        return False, str(e)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run ssh-keygen: {type(e).__name__}: {e}"
    if r.returncode == 0:
        return True, "verified"
    why = r.stderr.decode("utf-8", "replace").strip().splitlines()
    return False, (why[-1] if why else f"ssh-keygen exit code {r.returncode}")[:300]


def cli_sign(args) -> int:
    """`repomarshal waive-sign TARGET --reason R --repository O/R --pr N --head SHA --key K`:
    print the comment to post. Exit 0, or 1 with the reason on stderr."""
    target = str(args.target).strip()
    reason = " ".join(str(args.reason).split())
    head = str(args.head).strip().lower()
    try:
        msg = message(str(args.repository).strip(), args.pr, head, target, reason)
        token = sign(args.key, msg)
    except SignatureError as e:
        print(f"repomarshal waive-sign: {e}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as e:
        print(f"repomarshal waive-sign: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"Post this as a NEW comment on {args.repository}#{args.pr} (as prose, unedited), "
          f"then add the label repomarshal-waive:{target}. It is valid for head {head[:12]} "
          "only: after another push, sign again.", file=sys.stderr)  # fmt: skip
    print(f"repomarshal-waive {target}: {reason}")
    print(f"{SIG_KEYWORD} {target} {token}")
    return 0
