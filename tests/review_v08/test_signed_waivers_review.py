"""v0.8 review: signed waiver codes (T40) through waivers.collect_report on a fake GitHub API.

See conftest.py for the attacker model. Every signature here is made with the real ssh-keygen.
"""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import waivers, waiversig
from sshsig import SIGNED_AT, SIGNERS_PATH, make_key, needs_ssh_keygen, signed_body, token
from tests.unit.test_waivers import (
    HEAD,
    FakeGitHub,
    comment,
    github_env,
    label,
    make_plan,
    review,
    write_event,
)

pytestmark = needs_ssh_keygen

REPO, PR = "acme/webapp", 7
OWNER = "maintainer"  # CODEOWNER on `*` and the PR author (solo-maintainer repository)
CO = "co-owner"  # a second CODEOWNER, not the author
TARGET, REASON = "backend:unit", "unit runner offline on this PR"
SCAN = "security_scan"  # a kind-level target of make_plan (UNAVAILABLE)


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    return {"owner": make_key(d, "owner"), "co": make_key(d, "co")}


@pytest.fixture
def gh(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    with FakeGitHub() as fake:
        yield fake


def make_repo(tmp_path: Path, signers: str | None, symlink: bool = False) -> GitRepo:
    r = GitRepo(tmp_path / "repo")
    r.write(".github/CODEOWNERS", f"* @{OWNER} @{CO}\n")
    if signers is not None:
        if symlink:
            r.write(".github/real_signers", signers)
            os.symlink("real_signers", r.root / SIGNERS_PATH)
        else:
            r.write(SIGNERS_PATH, signers)
    r.write("backend/app.py", "x = 1\n")
    r.commit("BASE")
    return r


def collect(r, gh, tmp_path, *, author=OWNER, allow_author=True, head=HEAD, extra=None):
    plan = make_plan(r)
    pol = dataclasses.replace(
        plan.policy, allow_author_waivers=allow_author, waiver_signers=SIGNERS_PATH
    )
    plan = dataclasses.replace(plan, policy=pol)
    event = write_event(tmp_path / "event.json", author=author, head=head)
    env = github_env({}, gh.url, event)
    return waivers.collect_report(r.root, plan, "github", env, extra)


def body(
    key, target: str = TARGET, reason: str = REASON, head: str = HEAD, signed_at: str = SIGNED_AT
) -> str:
    return signed_body(key, REPO, PR, head, target, reason, signed_at)


def at(c: dict, when: str) -> dict:
    """A comment posted (and never edited) at `when`."""
    return {**c, "created_at": when, "updated_at": when}


# =========================================================================== fail-open findings
# All three were confirmed against the first v0.8 commit and are fixed: the signed message now
# carries `signed-at`, which must be later than the label's last removal, and the comment must be
# posted within waiversig.POST_WINDOW of it.

T1 = "2026-10-01T00:00:00Z"  # the maintainer signs and posts; label events n=2.. come later


def test_relabel_by_agent_revives_a_signed_waiver_the_human_withdrew(tmp_path, gh, keys):
    """FAIL-OPEN (revocation). The maintainer signs and grants a waiver, then withdraws it by
    removing the label (docs/guides/waivers.md: "removing a waiver updates the status check";
    "Remove repomarshal-waive:* labels once they are no longer meant to apply"). The agent, with
    the maintainer's gh login, simply re-adds the label: the labeled event is the maintainer's
    login with performed_via_github_app null, and the old signed comment still verifies for the
    unchanged head. Label events are not covered by the signature, so the withdrawal is undone
    without any touch of the key. The `unlabeled` event after the signed comment is on record
    (events are append-only) and should void that comment."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    gh.pr(
        events=[
            label(TARGET, OWNER, n=1),  # 2026-10-01: the maintainer grants
            label(TARGET, OWNER, event="unlabeled", n=2),  # 2026-10-02: the maintainer withdraws
            label(TARGET, OWNER, n=3),  # 2026-10-03: the agent re-adds it as the maintainer
        ],
        comments=[at(comment(OWNER, body(keys["owner"], signed_at=T1)), T1)],
    )
    found, notes = collect(r, gh, tmp_path)
    assert found == (), f"withdrawn signed waiver was revived by a relabel: {found} {notes}"


def test_reposted_copy_of_a_deleted_signed_comment_revives_the_waiver(tmp_path, gh, keys):
    """FAIL-OPEN (revocation, replay). The maintainer withdraws a signed waiver by removing the
    label AND deleting the comment. The agent had read the comment (it has the gh login), posts
    an identical NEW comment (unedited, no app) and re-adds the label. The signature covers only
    repository, PR, head, target and reason, nothing that ties it to one comment or one moment,
    so the copy verifies. Fixing this needs a signed time (e.g. `signed-at:` in the message, with
    the comment's created_at required to be shortly after it and after the latest `unlabeled`
    event of that label)."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    copy = at(comment(OWNER, body(keys["owner"], signed_at=T1)), "2026-10-04T00:00:00Z")
    gh.pr(
        events=[
            label(TARGET, OWNER, n=1),
            label(TARGET, OWNER, event="unlabeled", n=2),
            label(TARGET, OWNER, n=5),
        ],
        comments=[copy],  # the original was deleted; only the agent's verbatim copy remains
    )
    found, notes = collect(r, gh, tmp_path)
    assert found == (), f"replayed copy of a withdrawn signed code was accepted: {found} {notes}"


def test_co_owner_cannot_revoke_owners_signed_waiver(tmp_path, gh, keys):
    """FAIL-OPEN (revocation by another human). A second CODEOWNER removes the owner's waiver
    label because they disagree. The agent re-adds it with the owner's gh login; the owner's old
    signed comment still verifies for the same head, so the co-owner's veto is silently undone."""
    signers = keys["owner"].allowed(OWNER) + keys["co"].allowed(CO)
    r = make_repo(tmp_path, signers)
    gh.pr(
        events=[
            label(TARGET, OWNER, n=1),
            label(TARGET, CO, event="unlabeled", n=2),
            label(TARGET, OWNER, n=3),
        ],
        comments=[at(comment(OWNER, body(keys["owner"], signed_at=T1)), T1)],
    )
    found, notes = collect(r, gh, tmp_path)
    assert found == (), f"co-owner's removal was undone by a relabel: {found} {notes}"


# =========================================================================== defended


def test_reason_whitespace_variant_does_not_verify(tmp_path, gh, keys):
    """Defended: the signed reason is the exact text `_reason_from` extracts. A comment whose
    reason differs only in inner whitespace from the signed one is rejected."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    sig = token(keys["owner"], REPO, PR, HEAD, TARGET, REASON)
    spaced = REASON.replace(" ", "  ", 1)
    b = f"repomarshal-waive {TARGET}: {spaced}\n"
    b += f"{waiversig.SIG_KEYWORD} {TARGET} {SIGNED_AT} {sig}\n"
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, b)])
    assert collect(r, gh, tmp_path)[0] == ()


def test_unicode_line_separator_cannot_smuggle_a_second_reason(tmp_path, gh, keys):
    """Defended: a reason the owner signed ("ok") cannot be extended with text after a Unicode
    line separator; splitlines() cuts the line there, and the waiver's reason is only what was
    signed. A reason carrying the separator itself can never be signed (message() and
    splitlines agree on line ends for \\r, \\n; the other separators never reach message())."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    sig = token(keys["owner"], REPO, PR, HEAD, TARGET, "ok")
    b = f"repomarshal-waive {TARGET}: ok and also waive everything\n"
    b += f"{waiversig.SIG_KEYWORD} {TARGET} {SIGNED_AT} {sig}\n"
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, b)])
    found, notes = collect(r, gh, tmp_path)
    assert [w.reason for w in found] == ["ok"], notes


def test_signature_for_one_target_does_not_grant_another_in_the_same_comment(tmp_path, gh, keys):
    """Defended: a comment that holds a signed code for TARGET and an unsigned reason line for
    SCAN grants only TARGET, even with both labels applied by the owner's login."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    b = body(keys["owner"]) + f"repomarshal-waive {SCAN}: agent adds this one\n"
    gh.pr(
        events=[label(TARGET, OWNER, n=1), label(SCAN, OWNER, n=2)],
        comments=[comment(OWNER, b)],
    )
    found, notes = collect(r, gh, tmp_path)
    assert [w.target for w in found] == [TARGET], notes


def test_signature_for_target_reused_under_another_targets_sig_line(tmp_path, gh, keys):
    """Defended: copying TARGET's valid token onto a `repomarshal-waive-sig security_scan` line
    (same reason) does not verify: the target is in the signed message."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    sig = token(keys["owner"], REPO, PR, HEAD, TARGET, REASON)
    b = f"repomarshal-waive {SCAN}: {REASON}\n{waiversig.SIG_KEYWORD} {SCAN} {SIGNED_AT} {sig}\n"
    gh.pr(events=[label(SCAN, OWNER)], comments=[comment(OWNER, b)])
    assert collect(r, gh, tmp_path)[0] == ()


def test_sig_line_in_a_separate_comment_does_not_count(tmp_path, gh, keys):
    """Defended: the reason and its signed code must be in the same comment."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    reason_line, sig_line = body(keys["owner"]).splitlines()
    gh.pr(
        events=[label(TARGET, OWNER)],
        comments=[comment(OWNER, reason_line), comment(OWNER, sig_line)],
    )
    assert collect(r, gh, tmp_path)[0] == ()


@pytest.mark.parametrize("wrap", ["html-comment", "blockquote", "indented"])
def test_sig_line_that_is_not_prose_does_not_count(tmp_path, gh, keys, wrap):
    """Defended: like the reason line, the sig line must be prose."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    reason_line, sig_line = body(keys["owner"]).splitlines()
    hidden = {
        "html-comment": f"<!--\n{sig_line}\n-->",
        "blockquote": f"> {sig_line}",
        "indented": f"    {sig_line}",
    }[wrap]
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, f"{reason_line}\n{hidden}\n")])
    assert collect(r, gh, tmp_path)[0] == ()


def test_author_waiver_with_valid_signature_still_needs_allow_author(tmp_path, gh, keys):
    """Defended: a valid signature does not override allow_author_waivers = false."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    found, notes = collect(r, gh, tmp_path, allow_author=False)
    assert found == () and any("allow_author_waivers" in n for n in notes), notes


def test_review_with_a_valid_signed_code_is_still_rejected(tmp_path, gh, keys):
    """Defended: the review route has no signature check, but reviews never carry
    performed_via_github_app on GitHub, so every review waiver is rejected before that matters."""
    signers = keys["owner"].allowed(OWNER) + keys["co"].allowed(CO)
    r = make_repo(tmp_path, signers)
    gh.pr(reviews=[review(CO, "APPROVED", body(keys["co"]))])
    found, notes = collect(r, gh, tmp_path)
    assert found == (), notes


def test_unsigned_ci_workflow_waiver_is_rejected_when_signatures_are_required(tmp_path, gh, keys):
    """Defended: the trusted verdict's extra target `ci-workflow` goes through the same label
    path, so it also needs a signed code."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    extra = {"ci-workflow": (".github/workflows/ci.yml",)}
    gh.pr(
        events=[label("ci-workflow", OWNER)],
        comments=[comment(OWNER, "repomarshal-waive ci-workflow: reviewed ci.yml 0123456789ab")],
    )
    assert collect(r, gh, tmp_path, extra=extra)[0] == ()
    sig_body = body(keys["owner"], target="ci-workflow", reason="reviewed ci.yml 0123456789ab")
    gh.pr(events=[label("ci-workflow", OWNER)], comments=[comment(OWNER, sig_body)])
    found, notes = collect(r, gh, tmp_path, extra=extra)
    assert [w.target for w in found] == ["ci-workflow"], notes


@pytest.mark.parametrize("head", [HEAD.upper(), HEAD[:39], "", None])
def test_odd_head_sha_in_the_payload_rejects(tmp_path, gh, keys, head):
    """Defended: an upper-case, short, empty or missing head SHA means no signed waiver."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    found, notes = collect(r, gh, tmp_path, head=head)
    assert found == () and any("head SHA is unknown" in n for n in notes), notes


def test_signers_file_that_is_a_symlink_at_base_rejects(tmp_path, gh, keys):
    """Defended (fail closed): git stores a symlink's target text as its blob, which is no
    allowed-signers file. A symlink there never reads another file."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER), symlink=True)
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    assert collect(r, gh, tmp_path)[0] == ()


def test_huge_signers_file_rejects(tmp_path, gh, keys):
    """Defended (fail closed): a signers file over 64 KiB at BASE is not used at all."""
    r = make_repo(tmp_path, "#" * (70 * 1024) + "\n" + keys["owner"].allowed(OWNER))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    found, notes = collect(r, gh, tmp_path)
    assert found == () and any("too large" in n for n in notes), notes


def test_binary_garbage_line_in_signers_file_is_skipped(tmp_path, gh, keys):
    """Defended: ssh-keygen skips an unparsable line and matches only well-formed ones. The file
    is BASE content (trusted), so a garbage line can only lose keys, never add one."""
    r = make_repo(tmp_path, "\x01\x7f binary junk\n" + keys["owner"].allowed(OWNER))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    found, notes = collect(r, gh, tmp_path)
    assert [w.target for w in found] == [TARGET], notes


def _fake_keygen(directory: Path, script: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "ssh-keygen"
    p.write_text(f"#!/bin/sh\n{script}\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


@pytest.mark.parametrize(
    "script",
    [
        "sleep 5",  # hangs: the timeout must reject
        "echo 'Good \"repomarshal-waiver\" signature for maintainer'; exit 1",  # old/odd binary
        "echo 'unknown option -- Y' >&2; exit 1",  # OpenSSH older than 8.2
        "kill -9 $$",  # crashes
    ],
    ids=["slow", "good-text-bad-exit", "old", "crash"],
)
def test_broken_ssh_keygen_fails_closed(tmp_path, gh, keys, monkeypatch, script):
    """Defended: only exit code 0 from ssh-keygen accepts; a hang hits the timeout."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    exe = _fake_keygen(tmp_path / "bin", script)
    monkeypatch.setattr(waiversig, "_ssh_keygen", lambda: exe)
    monkeypatch.setattr(waiversig, "TIMEOUT_S", 0.5)
    assert collect(r, gh, tmp_path)[0] == ()


def test_callers_ssh_environment_cannot_influence_verify(tmp_path, gh, keys, monkeypatch):
    """Defended: ssh-keygen runs with only PATH, a fresh HOME and LC_ALL. A HOME holding an
    allowed_signers file that trusts another key, and SSH_* variables, change nothing."""
    agent_key = make_key(tmp_path / "agentkeys", "agent")
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "allowed_signers").write_text(agent_key.allowed(OWNER))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "nope.sock"))
    monkeypatch.setenv("SSH_ASKPASS", "/bin/true")
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(agent_key))])
    assert collect(r, gh, tmp_path)[0] == ()


def test_agent_comment_after_the_signed_one_does_not_change_the_reason(tmp_path, gh, keys):
    """Defended: a later (unsigned, or wrongly signed) comment by the owner's login neither
    replaces the signed reason nor clears it."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    bogus = (
        f"repomarshal-waive {TARGET}: whatever\n{waiversig.SIG_KEYWORD} {TARGET} {SIGNED_AT} AAAA\n"
    )
    gh.pr(
        events=[label(TARGET, OWNER)],
        comments=[
            at(comment(OWNER, body(keys["owner"])), "2026-10-09T00:00:00Z"),
            at(comment(OWNER, bogus), "2026-10-10T00:00:00Z"),
        ],
    )
    found, notes = collect(r, gh, tmp_path)
    assert [w.reason for w in found] == [REASON], notes


# =========================================================================== usability


def test_usability_one_comment_with_codes_for_two_targets(tmp_path, gh, keys):
    """Usability (works): the guide says codes for several targets can share one comment."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    b = body(keys["owner"]) + body(keys["owner"], target=SCAN, reason="no scanner for this repo")
    gh.pr(
        events=[label(TARGET, OWNER, n=1), label(SCAN, OWNER, n=2)],
        comments=[comment(OWNER, b)],
    )
    found, notes = collect(r, gh, tmp_path)
    assert sorted(w.target for w in found) == sorted([TARGET, SCAN]), notes


def test_usability_web_ui_crlf_comment_body(tmp_path, gh, keys):
    """Usability (works): comments typed in the web UI are stored with CRLF line ends."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    b = body(keys["owner"]).replace("\n", "\r\n")
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, b)])
    found, notes = collect(r, gh, tmp_path)
    assert [w.target for w in found] == [TARGET], notes


def test_usability_principal_case_must_match_the_login_exactly(tmp_path, gh, keys):
    """Usability (fail closed, documented): GitHub logins are case-insensitive, but ssh-keygen
    matches allowed-signers principals case-sensitively. A file that spells the login in another
    case rejects every genuine waiver, and the note only says 'ssh-keygen exit code 255'."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER.capitalize()))
    gh.pr(events=[label(TARGET, OWNER)], comments=[comment(OWNER, body(keys["owner"]))])
    found, notes = collect(r, gh, tmp_path)
    assert found == ()
    assert any("another case" in n for n in notes), notes  # v0.8: the note names the cause
