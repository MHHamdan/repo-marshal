"""Waiver identity (v0.4): a waiver needs a `repomarshal-waive:<target>` label currently on the
PR and a reason comment `repomarshal-waive <target>: <reason>`, both by the same human, both
with `performed_via_github_app` present and null. Reviews never grant. The PR author grants only
when the BASE policy sets completion.allow_author_waivers.

Threat: a coding agent acting through a GitHub App under the maintainer's own account. Its
labels and comments carry the maintainer's login; only performed_via_github_app tells them apart.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import api, waivers
from tests.unit.test_waivers import (
    AUTHOR,
    OWNER,
    FakeGitHub,
    comment,
    github_env,
    label,
    make_plan,
    review,
    user,
    write_event,
)

TARGET = "backend:unit"
APP = {"performed_via_github_app": {"slug": "claude", "name": "Claude"}}


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        yield fake


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    r = GitRepo(tmp_path / "repo")
    r.write(".github/CODEOWNERS", f"* @{OWNER}\n")
    r.write("backend/app.py", "x = 1\n")
    r.commit("BASE")
    return r


def collect(repo, gh, tmp_path, author=AUTHOR, allow_author=False, labels=None):
    plan = make_plan(repo)
    plan = dataclasses.replace(
        plan, policy=dataclasses.replace(plan.policy, allow_author_waivers=allow_author)
    )
    event = write_event(tmp_path / "event.json", author=author)
    if labels is not None:  # the PR's labels as GitHub reports them now
        payload = json.loads(event.read_text())
        payload["pull_request"]["labels"] = [{"name": n} for n in labels]
        event.write_text(json.dumps(payload))
        gh.routes["/repos/acme/webapp/issues/7/labels"] = [{"name": n} for n in labels]
    return waivers.collect_report(repo.root, plan, "github", github_env({}, gh.url, event))


def reason(login: str = OWNER, text: str = "runner offline", **extra) -> dict:
    return {**comment(login, f"repomarshal-waive {TARGET}: {text}"), **extra}


# --------------------------------------------------------------------------- fail-open


@pytest.mark.failopen
def test_reason_comment_edited_after_it_was_posted_is_not_a_reason(repo, gh, tmp_path):
    """FAIL-OPEN: performed_via_github_app records who CREATED a comment. The maintainer posted
    an unrelated comment as a person (field null); the agent, acting through the app under the
    maintainer's login, later edits that comment to add the reason line. The edit leaves the
    field null, so the edited text supplies the reason the human never wrote. Secure: a comment
    edited after creation (updated_at != created_at) cannot supply a waiver reason."""
    edited = comment(OWNER, "LGTM on the API shape.\n\nrepomarshal-waive backend:unit: flaky")
    edited.update(created_at="2026-10-02T00:00:00Z", updated_at="2026-10-09T12:00:00Z")
    gh.pr(events=[label(TARGET, OWNER)], comments=[edited])
    found, notes = collect(repo, gh, tmp_path)
    assert found == (), notes


@pytest.mark.failopen
def test_label_no_longer_on_the_pr_grants_nothing(repo, gh, tmp_path):
    """FAIL-OPEN (low): the docs say the label must be CURRENTLY on the PR, but the code infers
    that from events keyed by the exact, case-sensitive label name. GitHub label names are
    case-insensitive: after the label is renamed to another case (any writer, or the agent via
    the app), the human's removal is an `unlabeled` event for "Repomarshal-waive:..." that never
    matches the earlier `labeled` event, and the waiver outlives its retraction. (A label deleted
    from the repository likewise leaves no matching event.) Secure: no waiver for a label that
    the PR does not carry now."""
    removed = label(TARGET, OWNER, event="unlabeled", n=3)
    removed["label"] = {"name": f"Repomarshal-waive:{TARGET}"}
    gh.pr(events=[label(TARGET, OWNER, n=1), removed], comments=[reason()])
    found, notes = collect(repo, gh, tmp_path, labels=[])
    assert found == (), notes


@pytest.mark.failopen
def test_reason_line_quoted_in_a_code_block_is_not_a_reason(repo, gh, tmp_path):
    """FAIL-OPEN (low): a maintainer quotes the waiver syntax inside a fenced code block (e.g.
    explaining what the agent proposed and rejecting it). The regex matches lines inside the
    fence, so the quoted line becomes the maintainer's reason once the label is applied for any
    purpose. Secure: lines inside fenced or indented code blocks are not waiver lines."""
    quoted = comment(
        OWNER,
        "The agent proposed:\n\n```\nrepomarshal-waive backend:unit: tests are flaky\n```\n\n"
        "No: fix the tests.",
    )
    gh.pr(events=[label(TARGET, OWNER)], comments=[quoted])
    found, notes = collect(repo, gh, tmp_path)
    assert found == (), notes


# --------------------------------------------------------------------------- defended


def test_app_unlabel_then_relabel_after_a_human_label_is_rejected(repo, gh, tmp_path):
    """Defended: the latest `labeled` event decides; the app relabel replaces the human's."""
    gh.pr(
        events=[
            label(TARGET, OWNER, n=1),
            label(TARGET, OWNER, event="unlabeled", n=2, via=APP),
            label(TARGET, OWNER, n=3, via=APP),
        ],
        comments=[reason()],
    )
    found, notes = collect(repo, gh, tmp_path)
    assert found == ()
    assert any("through claude" in n for n in notes), notes


def test_human_label_with_only_an_app_reason_comment_is_rejected(repo, gh, tmp_path):
    """Defended: a later app comment does not supply the reason, an earlier one neither."""
    gh.pr(events=[label(TARGET, OWNER)], comments=[{**reason(), **APP}])
    found, notes = collect(repo, gh, tmp_path)
    assert found == ()
    assert any("GitHub App" in n for n in notes), notes


@pytest.mark.parametrize("field", ["missing", "empty-object", "false", "string"])
def test_odd_performed_via_values_on_the_label_event_are_rejected(repo, gh, tmp_path, field):
    """Defended: only a present, JSON-null performed_via_github_app counts as a person."""
    ev = label(TARGET, OWNER)
    if field == "missing":
        ev.pop("performed_via_github_app")
    else:
        ev["performed_via_github_app"] = {"empty-object": {}, "false": False, "string": ""}[field]
    gh.pr(events=[ev], comments=[reason()])
    found, _ = collect(repo, gh, tmp_path)
    assert found == ()


@pytest.mark.parametrize(
    "name, line",
    [
        ("repomarshal-waive:Backend:unit", "repomarshal-waive Backend:unit: x"),
        ("repomarshal-waive:backend:unit", "repomarshal-waive Backend:unit: x"),
        ("repomarshal-waive:backend:unit", "repomarshal-waive backend:unit:"),
        ("repomarshal-waive:backend:unit", "repomarshal-waive backend:unitx: y"),
    ],
)
def test_case_and_whitespace_variants_of_the_target_do_not_match(repo, gh, tmp_path, name, line):
    """Defended: gate ids match exactly; an empty reason is no reason."""
    ev = label(TARGET, OWNER)
    ev["label"] = {"name": name}
    gh.pr(events=[ev], comments=[comment(OWNER, line)])
    found, _ = collect(repo, gh, tmp_path)
    assert found == ()


def test_review_never_grants_even_with_a_null_field(repo, gh, tmp_path):
    """Defended in practice: reviews are rejected because the API sends no field. (If GitHub
    ever added `performed_via_github_app: null` to reviews they would be accepted again.)"""
    gh.pr(reviews=[review(OWNER, "APPROVED", f"repomarshal-waive {TARGET}: ok")])
    found, notes = collect(repo, gh, tmp_path)
    assert found == ()


def test_author_bot_with_allow_author_waivers_is_rejected(repo, gh, tmp_path):
    """Defended: allow_author_waivers lifts the author rule only for a human; a bot author's
    own label (null field, as for an installation token) is still a bot."""
    bot = "claude[bot]"
    gh.pr(events=[label(TARGET, bot)], comments=[reason(bot)])
    found, _ = collect(repo, gh, tmp_path, author=bot, allow_author=True)
    assert found == ()


def test_author_through_app_with_allow_author_waivers_is_rejected(repo, gh, tmp_path):
    """Defended: the author's label made through the app is rejected even when author waivers
    are allowed."""
    gh.pr(events=[label(TARGET, OWNER, via=APP)], comments=[reason()])
    found, _ = collect(repo, gh, tmp_path, author=OWNER, allow_author=True)
    assert found == ()


def test_allow_author_waivers_is_read_from_base_not_the_pr_head(tmp_path):
    """Defended: a PR that turns on allow_author_waivers (and waivers_permit_pass) in its own
    repomarshal.toml does not change the policy the waivers are judged by."""
    r = GitRepo(tmp_path / "repo")
    r.write("repomarshal.toml", "version = 1\n")
    r.write("app.py", "x = 1\n")
    base = r.commit("BASE")
    r.write(
        "repomarshal.toml",
        "version = 1\n[completion]\nallow_author_waivers = true\nwaivers_permit_pass = true\n",
    )
    r.write("app.py", "x = 2\n")
    r.commit("PR")
    p = api.plan(r.root, base=base)
    assert p.policy.allow_author_waivers is False
    assert p.policy.waivers_permit_pass is False


def test_events_beyond_the_page_limit_fail_closed(repo, gh, tmp_path, monkeypatch):
    """Defended: a listing longer than MAX_PAGES is an error (no waivers), not a truncation
    that could hide a later app relabel."""
    monkeypatch.setattr(waivers, "MAX_PAGES", 1)
    full = [label(TARGET, OWNER, n=1)] + [
        {"event": "commented", "actor": user(OWNER), "id": i} for i in range(99)
    ]
    gh.pr(events=full, comments=[reason()])
    found, notes = collect(repo, gh, tmp_path)
    assert found == ()
    assert any("pages" in n for n in notes), notes
