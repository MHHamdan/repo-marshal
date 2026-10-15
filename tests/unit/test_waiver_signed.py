"""v0.8 signed waiver codes (threat T40), through waivers.collect_report on a fake GitHub API.

T40: a local agent that uses the maintainer's own `gh` login or personal access token makes label
and comment events with `performed_via_github_app: null`, exactly like the maintainer in the web
UI. With `completion.waiver_signers` at BASE, a label waiver also needs a code signed with a key
listed there for the commenter's login, over the repository, PR, head SHA, target and reason.
"""

from __future__ import annotations

import dataclasses
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
    write_event,
)

pytestmark = needs_ssh_keygen

REPO, PR = "acme/webapp", 7
OWNER = "maintainer"  # the only CODEOWNER, and the PR author
CO = "co-owner"  # a second CODEOWNER, not the author
TARGET, REASON = "backend:unit", "unit runner offline on this PR"
OTHER_HEAD = "d" * 40


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("keys")
    return {"owner": make_key(d, "owner"), "co": make_key(d, "co"), "agent": make_key(d, "agent")}


@pytest.fixture
def gh(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    with FakeGitHub() as fake:
        yield fake


def make_repo(tmp_path: Path, signers: str | None) -> GitRepo:
    r = GitRepo(tmp_path / "repo")
    r.write(".github/CODEOWNERS", f"* @{OWNER} @{CO}\n")
    if signers is not None:
        r.write(SIGNERS_PATH, signers)
    r.write("backend/app.py", "x = 1\n")
    r.commit("BASE")
    return r


def collect(
    r: GitRepo,
    gh: FakeGitHub,
    tmp_path: Path,
    *,
    author: str = OWNER,
    signers_path: str = SIGNERS_PATH,
    allow_author: bool = True,
    head: str = HEAD,
):
    plan = make_plan(r)
    pol = dataclasses.replace(
        plan.policy, allow_author_waivers=allow_author, waiver_signers=signers_path
    )
    plan = dataclasses.replace(plan, policy=pol)
    event = write_event(tmp_path / "event.json", author=author, head=head)
    return waivers.collect_report(r.root, plan, "github", github_env({}, gh.url, event))


def body(key, login_head=HEAD, target=TARGET, reason=REASON, pr=PR, signed_at=SIGNED_AT):
    return signed_body(key, REPO, pr, login_head, target, reason, signed_at)


def grant(gh: FakeGitHub, who: str, *bodies: str, **comment_kw) -> None:
    gh.pr(events=[label(TARGET, who)], comments=[comment(who, b, **comment_kw) for b in bodies])


# --------------------------------------------------------------------------- accepted


def test_signed_author_waiver_is_accepted(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]))
    found, notes = collect(r, gh, tmp_path)
    assert [(w.target, w.identity, w.reason) for w in found] == [
        (TARGET, f"{OWNER} (ssh-signed)", REASON)
    ], notes
    assert any("SSH-signed code by maintainer verified" in n for n in notes)


def test_signed_co_owner_waiver_is_accepted(tmp_path, gh, keys):
    signers = keys["owner"].allowed(OWNER) + keys["co"].allowed(CO)
    r = make_repo(tmp_path, signers)
    grant(gh, CO, body(keys["co"]))
    found, notes = collect(r, gh, tmp_path)
    assert [w.identity for w in found] == [f"{CO} (ssh-signed)"], notes


def test_latest_valid_signed_comment_wins_over_a_later_unsigned_one(tmp_path, gh, keys):
    """A later unsigned (or agent-written) comment cannot displace a valid signed one."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]), f"repomarshal-waive {TARGET}: agent says it is fine")
    found, notes = collect(r, gh, tmp_path)
    assert [w.reason for w in found] == [REASON], notes


# --------------------------------------------------------------------------- the T40 attack


def test_t40_agent_with_the_owners_gh_login_cannot_self_grant(tmp_path, gh, keys):
    """What a local agent can do with the owner's gh CLI: a human-looking label and comment
    (performed_via_github_app null), but no signature from the owner's key."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, f"repomarshal-waive {TARGET}: {REASON}")
    found, notes = collect(r, gh, tmp_path)
    assert found == ()
    assert any("no 'repomarshal-waive-sig" in n for n in notes), notes


def test_t40_agent_signing_with_its_own_key_is_rejected(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["agent"]))
    found, notes = collect(r, gh, tmp_path)
    assert found == (), notes


def test_t40_author_waiver_without_signers_policy_is_rejected(tmp_path, gh, keys):
    """allow_author_waivers alone no longer lets the author waive: nothing distinguishes the
    author from an agent using the author's gh login."""
    r = make_repo(tmp_path, None)
    grant(gh, OWNER, f"repomarshal-waive {TARGET}: {REASON}")
    found, notes = collect(r, gh, tmp_path, signers_path="")
    assert found == ()
    assert any("T40" in n and "waiver_signers" in n for n in notes), notes


def test_non_author_without_signers_policy_is_unchanged(tmp_path, gh):
    """Repositories that do not opt in keep the v0.4 rules for other maintainers."""
    r = make_repo(tmp_path, None)
    grant(gh, CO, f"repomarshal-waive {TARGET}: {REASON}")
    found, notes = collect(r, gh, tmp_path, signers_path="", allow_author=False)
    assert [w.identity for w in found] == [CO], notes


# --------------------------------------------------------------------------- replay and binding


@pytest.mark.parametrize(
    "variant",
    ["old-head", "other-pr", "other-target", "other-reason"],
)
def test_signature_for_something_else_is_rejected(tmp_path, gh, keys, variant):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    k = keys["owner"]
    if variant == "old-head":
        b = body(k, login_head=OTHER_HEAD)
    elif variant == "other-pr":
        b = body(k, pr=PR + 1)
    elif variant == "other-target":
        sig = token(k, REPO, PR, HEAD, "frontend:unit", REASON)
        b = f"repomarshal-waive {TARGET}: {REASON}\n"
        b += f"{waiversig.SIG_KEYWORD} {TARGET} {SIGNED_AT} {sig}\n"
    else:
        sig = token(k, REPO, PR, HEAD, TARGET, "something milder")
        b = f"repomarshal-waive {TARGET}: {REASON}\n"
        b += f"{waiversig.SIG_KEYWORD} {TARGET} {SIGNED_AT} {sig}\n"
    grant(gh, OWNER, b)
    found, notes = collect(r, gh, tmp_path)
    assert found == (), notes
    assert any("does not verify" in n for n in notes), notes


def test_new_push_invalidates_the_waiver(tmp_path, gh, keys):
    """Signed for the old head; the PR now has a new head (also closes T33 for signed waivers)."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]))
    assert collect(r, gh, tmp_path, head=OTHER_HEAD)[0] == ()


def test_signature_of_another_login_is_rejected(tmp_path, gh, keys):
    """The co-owner's valid code, reposted by the owner's account, verifies for CO only."""
    signers = keys["owner"].allowed(OWNER) + keys["co"].allowed(CO)
    r = make_repo(tmp_path, signers)
    grant(gh, OWNER, body(keys["co"]))
    assert collect(r, gh, tmp_path)[0] == ()


# --------------------------------------------------------------------------- comment rules hold


def test_signed_comment_through_an_app_is_rejected(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]), via={"performed_via_github_app": {"slug": "claude"}})
    assert collect(r, gh, tmp_path)[0] == ()


def test_signed_comment_that_was_edited_is_rejected(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    c = comment(OWNER, body(keys["owner"]))
    c["updated_at"] = "2026-10-10T00:00:00Z"
    gh.pr(events=[label(TARGET, OWNER)], comments=[c])
    found, notes = collect(r, gh, tmp_path)
    assert found == () and any("edited" in n for n in notes), notes


def test_signature_inside_a_code_block_does_not_count(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    reason_line, sig_line = body(keys["owner"]).splitlines()
    grant(gh, OWNER, f"{reason_line}\n```\n{sig_line}\n```\n")
    assert collect(r, gh, tmp_path)[0] == ()


def test_signature_needs_the_label(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    gh.pr(events=[], comments=[comment(OWNER, body(keys["owner"]))])
    assert collect(r, gh, tmp_path)[0] == ()


# --------------------------------------------------------------------------- fail closed


def test_missing_signers_file_at_base_rejects(tmp_path, gh, keys):
    r = make_repo(tmp_path, None)
    grant(gh, OWNER, body(keys["owner"]))
    found, notes = collect(r, gh, tmp_path)
    assert found == ()
    assert any("does not exist at BASE" in n for n in notes), notes


def test_signers_file_added_by_the_pr_is_not_used(tmp_path, gh, keys):
    """The allowed-signers file is read at BASE: a PR that adds the agent's key gains nothing."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    plan_base = r.head()
    r.write(SIGNERS_PATH, keys["owner"].allowed(OWNER) + keys["agent"].allowed(OWNER))
    r.commit("PR: trust the agent's key")
    grant(gh, OWNER, body(keys["agent"]))
    plan = make_plan(r)
    plan = dataclasses.replace(
        plan,
        change=dataclasses.replace(plan.change, base=plan_base),
        policy=dataclasses.replace(
            plan.policy, allow_author_waivers=True, waiver_signers=SIGNERS_PATH
        ),
    )
    event = write_event(tmp_path / "event.json", author=OWNER)
    found, notes = waivers.collect_report(r.root, plan, "github", github_env({}, gh.url, event))
    assert found == (), notes


def test_unknown_head_rejects(tmp_path, gh, keys):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]))
    found, notes = collect(r, gh, tmp_path, head="not-a-sha")
    assert found == ()
    assert any("head SHA is unknown" in n for n in notes), notes


def test_missing_ssh_keygen_rejects(tmp_path, gh, keys, monkeypatch):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]))
    monkeypatch.setattr(waiversig.shutil, "which", lambda _name: None)
    found, notes = collect(r, gh, tmp_path)
    assert found == () and any("ssh-keygen" in n for n in notes), notes


# --------------------------------------------------------------------------- signed-at (review)


def _at(c: dict, when: str) -> dict:
    return {**c, "created_at": when, "updated_at": when}


@pytest.mark.parametrize(
    ("posted", "accepted"),
    [("2026-10-09T00:00:00Z", True), ("2026-10-09T00:29:59Z", True),
     ("2026-10-09T00:30:01Z", False), ("2026-10-08T23:55:00Z", True),
     ("2026-10-08T23:54:59Z", False), ("2026-11-09T00:00:00Z", False)],
    ids=["same", "29min", "31min", "skew-ok", "skew-too-big", "month-later"],
)  # fmt: skip
def test_comment_must_be_posted_soon_after_signing(tmp_path, gh, keys, posted, accepted):
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    c = _at(comment(OWNER, body(keys["owner"])), posted)
    gh.pr(events=[label(TARGET, OWNER)], comments=[c])
    found, notes = collect(r, gh, tmp_path)
    assert bool(found) is accepted, notes


def test_regrant_after_a_withdrawal_needs_and_accepts_a_fresh_code(tmp_path, gh, keys):
    """Grant, withdraw (unlabel), grant again: the old code stays void, a new one works."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    old = _at(comment(OWNER, body(keys["owner"], signed_at=SIGNED_AT)), SIGNED_AT)
    events = [
        {**label(TARGET, OWNER, n=1), "created_at": "2026-10-09T00:01:00Z"},
        {**label(TARGET, OWNER, event="unlabeled", n=2), "created_at": "2026-10-09T01:00:00Z"},
        {**label(TARGET, OWNER, n=3), "created_at": "2026-10-09T02:00:00Z"},
    ]
    gh.pr(events=events, comments=[old])
    found, notes = collect(r, gh, tmp_path)
    assert found == () and any("before the label was last removed" in n for n in notes), notes
    fresh = "2026-10-09T01:50:00Z"
    new = _at(comment(OWNER, body(keys["owner"], signed_at=fresh)), "2026-10-09T01:51:00Z")
    gh.pr(events=events, comments=[old, new])
    assert [w.target for w in collect(r, gh, tmp_path)[0]] == [TARGET]


def test_any_valid_sig_line_for_the_target_counts(tmp_path, gh, keys):
    """A stale code and a fresh one in the same comment: the fresh one is used."""
    r = make_repo(tmp_path, keys["owner"].allowed(OWNER))
    stale = body(keys["owner"], login_head=OTHER_HEAD).splitlines()[1]
    good = body(keys["owner"])
    grant(gh, OWNER, good.replace("\n", f"\n{stale}\n", 1))
    assert [w.target for w in collect(r, gh, tmp_path)[0]] == [TARGET]


def test_signers_file_with_a_utf8_bom_works(tmp_path, gh, keys):
    r = make_repo(tmp_path, "﻿" + keys["owner"].allowed(OWNER))
    grant(gh, OWNER, body(keys["owner"]))
    assert [w.target for w in collect(r, gh, tmp_path)[0]] == [TARGET]


def test_relative_ssh_keygen_on_path_is_refused(monkeypatch):
    monkeypatch.setattr(waiversig.shutil, "which", lambda _name: "ssh-keygen")
    with pytest.raises(waiversig.SignatureError, match="relative"):
        waiversig._ssh_keygen()
