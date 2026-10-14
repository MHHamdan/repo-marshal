"""v0.4 waiver identity rules, on GitHub API payloads recorded from this repository.

1. Solo maintainer: the PR author may grant a label waiver only when the BASE policy sets
   completion.allow_author_waivers.
2. Agent vs human: an action made through a GitHub App carries the maintainer's own login, so
   `performed_via_github_app` decides. App value -> rejected; field missing -> rejected.

The recordings (tests/fixtures/github_api/, see its README) were made through the Claude GitHub
app. The "human" variant sets performed_via_github_app to null, as GitHub does for a person's
action on github.com; the "missing" variant removes the key. Nothing else is changed except the
label name and comment body, which are pointed at a target of the test plan.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import waivers
from repomarshal.model import WaiverSource
from tests.unit.test_waivers import FakeGitHub, github_env, make_plan, write_event

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github_api"
MAINTAINER = "MHHamdan"  # the recorded actor: the only CODEOWNER, and the PR author
TARGET = "backend:unit"
REASON = "unit runner offline on this PR"

HUMAN, APP, MISSING = "human", "app", "missing"


def _recorded(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _via(obj: dict, how: str) -> dict:
    out = copy.deepcopy(obj)
    if how == HUMAN:
        out["performed_via_github_app"] = None
    elif how == MISSING:
        out.pop("performed_via_github_app", None)
    else:
        assert out["performed_via_github_app"]["slug"] == "claude"  # the recording, untouched
    return out


def label_events(how: str) -> list[dict]:
    (ev,) = _recorded("app_labeled_events.json")
    assert ev["event"] == "labeled" and ev["actor"]["login"] == MAINTAINER
    ev = _via(ev, how)
    ev["label"]["name"] = f"repomarshal-waive:{TARGET}"
    return [ev]


def reason_comments(how: str) -> list[dict]:
    (c,) = _recorded("app_comments.json")
    assert c["user"]["login"] == MAINTAINER
    c = _via(c, how)
    c["body"] = f"repomarshal-waive {TARGET}: {REASON}"
    return [c]


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        yield fake


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    r = GitRepo(tmp_path / "repo")
    r.write(".github/CODEOWNERS", f"* @{MAINTAINER}\n")
    r.write("backend/app.py", "x = 1\n")
    r.commit("BASE")
    return r


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")


def collect(repo: GitRepo, gh: FakeGitHub, tmp_path: Path, allow_author: bool):
    plan = make_plan(repo)
    plan = dataclasses.replace(
        plan, policy=dataclasses.replace(plan.policy, allow_author_waivers=allow_author)
    )
    event = write_event(tmp_path / "event.json", author=MAINTAINER)
    env = github_env({}, gh.url, event)
    return waivers.collect_report(repo.root, plan, "github", env)


def test_recordings_attribute_the_app_to_the_maintainers_login():
    """The premise: through the app, the actor IS the maintainer's login."""
    (ev,) = _recorded("app_labeled_events.json")
    (c,) = _recorded("app_comments.json")
    for obj, who in ((ev, ev["actor"]), (c, c["user"])):
        assert who["login"] == MAINTAINER and who["type"] == "User"
        assert obj["performed_via_github_app"]["slug"] == "claude"


def test_human_author_waiver_is_rejected_without_the_policy(repo, gh, tmp_path):
    gh.pr(events=label_events(HUMAN), comments=reason_comments(HUMAN))
    found, notes = collect(repo, gh, tmp_path, allow_author=False)
    assert found == (), notes
    assert any("allow_author_waivers" in n for n in notes), notes


def test_human_author_waiver_is_allowed_with_the_policy(repo, gh, tmp_path):
    gh.pr(events=label_events(HUMAN), comments=reason_comments(HUMAN))
    found, notes = collect(repo, gh, tmp_path, allow_author=True)
    assert [(w.target, w.identity, w.source, w.reason) for w in found] == [
        (TARGET, MAINTAINER, WaiverSource.GITHUB_LABEL, REASON)
    ], notes


def test_label_applied_through_the_app_is_rejected(repo, gh, tmp_path):
    gh.pr(events=label_events(APP), comments=reason_comments(HUMAN))
    found, notes = collect(repo, gh, tmp_path, allow_author=True)
    assert found == (), notes
    assert any("through claude" in n for n in notes), notes


def test_reason_comment_made_through_the_app_is_rejected(repo, gh, tmp_path):
    gh.pr(events=label_events(HUMAN), comments=reason_comments(APP))
    found, notes = collect(repo, gh, tmp_path, allow_author=True)
    assert found == (), notes
    assert any("through a GitHub App" in n for n in notes), notes


def test_both_through_the_app_is_rejected(repo, gh, tmp_path):
    """The recordings exactly as GitHub returned them."""
    gh.pr(events=label_events(APP), comments=reason_comments(APP))
    assert collect(repo, gh, tmp_path, allow_author=True)[0] == ()


@pytest.mark.parametrize(
    ("label_how", "comment_how"), [(MISSING, HUMAN), (HUMAN, MISSING), (MISSING, MISSING)]
)
def test_missing_field_is_rejected(repo, gh, tmp_path, label_how, comment_how):
    gh.pr(events=label_events(label_how), comments=reason_comments(comment_how))
    found, notes = collect(repo, gh, tmp_path, allow_author=True)
    assert found == (), notes


def test_unreadable_field_is_rejected(repo, gh, tmp_path):
    """Anything but an explicit null (e.g. false, "", 0) is not a statement that no app was
    involved."""
    for odd in (False, "", 0, {}):
        ev = label_events(HUMAN)
        ev[0]["performed_via_github_app"] = odd
        gh.pr(events=ev, comments=reason_comments(HUMAN))
        assert collect(repo, gh, tmp_path, allow_author=True)[0] == (), odd


def test_human_comment_after_an_app_comment_supplies_the_reason(repo, gh, tmp_path):
    """An app comment is skipped, not fatal: a later comment the maintainer typed counts."""
    gh.pr(
        events=label_events(HUMAN),
        comments=reason_comments(APP) + reason_comments(HUMAN),
    )
    found, notes = collect(repo, gh, tmp_path, allow_author=True)
    assert [w.reason for w in found] == [REASON], notes


def test_review_waiver_is_rejected_even_from_a_human_author_with_the_policy(repo, gh, tmp_path):
    """The reviews API has no performed_via_github_app field: fail closed."""
    review = {
        "user": {"login": MAINTAINER, "type": "User"},
        "state": "APPROVED",
        "body": f"repomarshal-waive {TARGET}: {REASON}",
        "commit_id": "c0ffee" * 6 + "abcd",
        "submitted_at": "2026-10-14T00:00:00Z",
        "html_url": "https://github.com/MHHamdan/repo-marshal/pull/3#review",
    }
    gh.pr(reviews=[review])
    found, notes = collect(repo, gh, tmp_path, allow_author=True)
    assert found == (), notes
    assert any("reviews API does not say" in n for n in notes), notes
