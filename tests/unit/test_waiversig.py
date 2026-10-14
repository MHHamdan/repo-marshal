"""v0.8 (T40): the signed waiver code itself: message binding, verification and the CLI."""

from __future__ import annotations

import argparse
import base64

import pytest

from repomarshal import waiversig
from sshsig import make_key, needs_ssh_keygen, sign_raw, token

pytestmark = needs_ssh_keygen

REPO, PR, HEAD, TARGET, REASON = "acme/webapp", 7, "c" * 40, "backend:unit", "runner offline"
LOGIN = "maintainer"


@pytest.fixture(scope="module")
def key(tmp_path_factory):
    return make_key(tmp_path_factory.mktemp("keys"), "maintainer")


@pytest.fixture(scope="module")
def other(tmp_path_factory):
    return make_key(tmp_path_factory.mktemp("keys"), "other")


def msg(**kw):
    args = {"repository": REPO, "pr": PR, "head": HEAD, "target": TARGET, "reason": REASON}
    args.update(kw)
    return waiversig.message(**args)


def test_message_is_line_based_and_complete():
    text = msg().decode()
    assert text.splitlines() == [
        "repomarshal-waiver/1", f"repository: {REPO}", f"pr: {PR}", f"head: {HEAD}",
        f"target: {TARGET}", f"reason: {REASON}",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "kw",
    [{"repository": "no-slash"}, {"pr": 0}, {"pr": True}, {"pr": "7"}, {"head": "C" * 40},
     {"head": "c" * 39}, {"target": ""}, {"reason": ""}, {"reason": "a\nhead: " + "d" * 40},
     {"reason": " padded"}, {"target": "x\r"}],
)  # fmt: skip
def test_message_refuses_ambiguous_fields(kw):
    with pytest.raises(waiversig.SignatureError):
        msg(**kw)


def test_signature_verifies_for_the_listed_principal(key):
    ok, why = waiversig.verify(
        key.allowed(LOGIN).encode(), LOGIN, msg(), token(key, REPO, PR, HEAD, TARGET, REASON)
    )
    assert ok, why


@pytest.mark.parametrize(
    "change",
    [{"repository": "acme/other"}, {"pr": 8}, {"head": "d" * 40}, {"target": "frontend:unit"},
     {"reason": "a different reason"}],
    ids=["repo", "pr", "head", "target", "reason"],
)  # fmt: skip
def test_signature_is_bound_to_every_field(key, change):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    ok, _ = waiversig.verify(key.allowed(LOGIN).encode(), LOGIN, msg(**change), sig)
    assert not ok


def test_other_principal_or_unlisted_key_fails(key, other):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    signers = key.allowed(LOGIN).encode()
    assert not waiversig.verify(signers, "someone-else", msg(), sig)[0]
    # A key the agent made itself is not in the BASE file.
    forged = token(other, REPO, PR, HEAD, TARGET, REASON)
    assert not waiversig.verify(signers, LOGIN, msg(), forged)[0]
    # The other key is listed, but for another principal.
    both = (key.allowed(LOGIN) + other.allowed("someone-else")).encode()
    assert not waiversig.verify(both, LOGIN, msg(), forged)[0]


def test_signature_in_another_namespace_fails(key):
    """A signature the maintainer made for something else (e.g. a git commit, namespace "git")
    over the same bytes is not a waiver."""
    sig = sign_raw(key, msg(), "git")
    assert not waiversig.verify(key.allowed(LOGIN).encode(), LOGIN, msg(), sig)[0]


def test_allowed_signers_options_are_enforced(key):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    wrong_ns = key.allowed(LOGIN, 'namespaces="git"').encode()
    expired = key.allowed(LOGIN, 'valid-before="20000101"').encode()
    assert not waiversig.verify(wrong_ns, LOGIN, msg(), sig)[0]
    assert not waiversig.verify(expired, LOGIN, msg(), sig)[0]
    right_ns = key.allowed(LOGIN, f'namespaces="{waiversig.NAMESPACE}"').encode()
    assert waiversig.verify(right_ns, LOGIN, msg(), sig)[0]


@pytest.mark.parametrize(
    "bad",
    ["", "not base64!", "QUJD", base64.b64encode(b"SSHSIG").decode(), "A" * 9000,
     "U1NIU0lH\nAAAA"],
    ids=["empty", "junk", "not-sshsig", "truncated", "huge", "newline"],
)  # fmt: skip
def test_malformed_tokens_fail_without_raising(key, bad):
    ok, why = waiversig.verify(key.allowed(LOGIN).encode(), LOGIN, msg(), bad)
    assert not ok and why


def test_tampered_token_fails(key):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    blob = bytearray(base64.b64decode(sig))
    blob[-5] ^= 1
    assert not waiversig.verify(
        key.allowed(LOGIN).encode(), LOGIN, msg(), base64.b64encode(bytes(blob)).decode()
    )[0]


@pytest.mark.parametrize("principal", ["*", "-I", "a b", "", "x" * 40, "../x"])
def test_principal_must_be_a_login(key, principal):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    assert not waiversig.verify(key.allowed("*").encode(), principal, msg(), sig)[0]


def test_empty_or_huge_signers_file_fails(key):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    assert not waiversig.verify(b"  \n", LOGIN, msg(), sig)[0]
    huge = key.allowed(LOGIN).encode() + b"#" * (waiversig.MAX_SIGNERS_BYTES + 1)
    assert not waiversig.verify(huge, LOGIN, msg(), sig)[0]


def test_missing_ssh_keygen_fails_closed(key, monkeypatch):
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    monkeypatch.setattr(waiversig.shutil, "which", lambda _name: None)
    ok, why = waiversig.verify(key.allowed(LOGIN).encode(), LOGIN, msg(), sig)
    assert not ok and "ssh-keygen" in why


def test_verification_ignores_the_callers_ssh_agent(key, monkeypatch):
    """Only the given files decide: no agent socket or user config is passed to ssh-keygen."""
    monkeypatch.setenv("SSH_AUTH_SOCK", "/nonexistent/agent.sock")
    sig = token(key, REPO, PR, HEAD, TARGET, REASON)
    assert waiversig.verify(key.allowed(LOGIN).encode(), LOGIN, msg(), sig)[0]


def _args(signing_key, **kw):
    a = {"target": TARGET, "reason": "  runner   offline ", "repository": REPO, "pr": PR,
         "head": HEAD.upper(), "key": str(signing_key.private)}  # fmt: skip
    a.update(kw)
    return argparse.Namespace(**a)


def test_cli_prints_a_comment_that_verifies(key, capsys):
    assert waiversig.cli_sign(_args(key)) == 0
    reason_line, sig_line = capsys.readouterr().out.splitlines()
    assert reason_line == f"repomarshal-waive {TARGET}: {REASON}"  # whitespace collapsed
    kw, target, sig = sig_line.split(" ")
    assert (kw, target) == (waiversig.SIG_KEYWORD, TARGET)
    assert waiversig.verify(key.allowed(LOGIN).encode(), LOGIN, msg(), sig)[0]


@pytest.mark.parametrize("kw", [{"head": "abc"}, {"pr": -1}, {"repository": "x"}, {"key": "/nope"}])
def test_cli_refuses_bad_input(key, kw, capsys):
    assert waiversig.cli_sign(_args(key, **kw)) == 1
    assert capsys.readouterr().out == ""
