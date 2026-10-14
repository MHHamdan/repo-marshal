"""Waiver collection tests (owner: agent "override"; MISSION.md §54).

GitHub mode runs against a fake GitHub REST API: a local http.server thread serving canned JSON,
with GITHUB_API_URL pointed at it. No test touches the network.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from helpers import GitRepo
from repomarshal import waivers
from repomarshal.model import (
    ChangedFile,
    ChangeSet,
    ContextPlan,
    Cost,
    FileStatus,
    Gate,
    GateDecision,
    GateKind,
    Plan,
    PlanMode,
    PolicyRef,
    Reason,
    Risk,
    WaiverSource,
)
from repomarshal.model import DecisionStatus as D

SRC = Path(__file__).resolve().parents[2] / "src"

AUTHOR = "agent-dev"  # the PR author (the account the coding agent works as)
OWNER = "maint"  # on the `*` CODEOWNERS rule
BACKEND_OWNER = "be-owner"  # owns backend/ only
ADMIN = "admin-user"  # not a CODEOWNER, has admin permission
OUTSIDER = "outsider"  # not a CODEOWNER, write permission only
BOT = "github-actions[bot]"
HEAD = "c0ffee" * 6 + "abcd"
CODEOWNERS = f"* @{OWNER}\n/backend/ @{BACKEND_OWNER} @{OWNER}\n/docs/ @org/docs-team\n"


# --------------------------------------------------------------------------- fake GitHub


class FakeGitHub:
    """Serves `routes[path]` as JSON. A route value is a payload, (status, payload), or
    "timeout" (the handler sleeps longer than the client timeout). Page 2+ of a list is []."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.requests: list[str] = []
        self.auth: list[str | None] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                parts = urlsplit(self.path)
                fake.requests.append(parts.path)
                fake.auth.append(self.headers.get("Authorization"))
                page = int(parse_qs(parts.query).get("page", ["1"])[0])
                route = fake.routes.get(parts.path, (404, {"message": "Not Found"}))
                if route == "timeout":
                    time.sleep(2)
                    return
                status, body = route if isinstance(route, tuple) else (200, route)
                if page > 1 and isinstance(body, list):
                    body = []
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> FakeGitHub:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------------ canned PR state

    def pr(
        self,
        events: list | None = None,
        comments: list | None = None,
        reviews: list | None = None,
        permissions: dict[str, str] | None = None,
        repo: str = "acme/webapp",
        number: int = 7,
    ) -> None:
        self.routes[f"/repos/{repo}/issues/{number}/events"] = events or []
        self.routes[f"/repos/{repo}/issues/{number}/comments"] = comments or []
        self.routes[f"/repos/{repo}/pulls/{number}/reviews"] = reviews or []
        for login, role in (permissions or {}).items():
            path = f"/repos/{repo}/collaborators/{login}/permission"
            if role == "403":
                self.routes[path] = (403, {"message": "Resource not accessible"})
            else:
                perm = "write" if role == "maintain" else role
                self.routes[path] = {"permission": perm, "role_name": role}


def user(login: str) -> dict:
    return {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}


_NO_APP: dict = {"performed_via_github_app": None}  # what GitHub sends for a person's action


def label(
    target: str, login: str, event: str = "labeled", n: int = 1, via: dict | None = _NO_APP
) -> dict:
    return {
        "id": n,
        "event": event,
        "actor": user(login),
        "label": {"name": f"repomarshal-waive:{target}"},
        "created_at": f"2026-10-0{n}T00:00:00Z",
        **(via if via is not None else {}),
    }


def comment(login: str, body: str, via: dict | None = _NO_APP) -> dict:
    return {
        "user": user(login),
        "body": body,
        "html_url": f"https://gh/c/{login}",
        **(via if via is not None else {}),
    }


def review(login: str, state: str, body: str, commit: str = HEAD) -> dict:
    return {
        "user": user(login),
        "state": state,
        "body": body,
        "commit_id": commit,
        "submitted_at": "2026-10-10T00:00:00Z",
        "html_url": f"https://gh/r/{login}",
    }


def write_event(path: Path, author: str = AUTHOR, number: int = 7, head: str = HEAD) -> Path:
    path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": number,
                    "user": user(author),
                    "head": {"sha": head},
                    "base": {"sha": "0" * 40},
                },
                "repository": {"full_name": "acme/webapp"},
            }
        )
    )
    return path


def github_env(base: dict[str, str], api_url: str, event_path: Path, **extra: str) -> dict:
    """`base` minus any real CI variables, plus a pull_request event in GitHub Actions."""
    env = {k: v for k, v in base.items() if not k.startswith("GITHUB_") and k != "CI"}
    env.update(
        GITHUB_ACTIONS="true",
        GITHUB_EVENT_NAME="pull_request",
        GITHUB_EVENT_PATH=str(event_path),
        GITHUB_REPOSITORY="acme/webapp",
        GITHUB_TOKEN="test-token",  # noqa: S105 - fake
        GITHUB_API_URL=api_url,
    )
    env.update(extra)
    return env


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        yield fake


# --------------------------------------------------------------------------- plan under test


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    r = GitRepo(tmp_path / "repo")
    r.write(".github/CODEOWNERS", CODEOWNERS)
    r.write("backend/app.py", "x = 1\n")
    r.commit("BASE")
    return r


def make_plan(repo: GitRepo, tree: str = "t" * 40) -> Plan:
    base = repo.head()
    unit = Gate(
        "backend:unit", GateKind.UNIT, ("pytest",), "backend", "backend/", Cost.STANDARD, "t"
    )
    return Plan(
        mode=PlanMode.BINDING,
        task=None,
        change=ChangeSet(
            base, base, tree, (ChangedFile("backend/app.py", FileStatus.MODIFIED),), True
        ),
        signals=(),
        domains=(),
        risk=Risk.ELEVATED,
        inventory=(unit,),
        gates=(
            GateDecision(GateKind.UNIT, D.REQUIRED, (Reason("changed"),), "backend:unit"),
            GateDecision(GateKind.SECURITY_SCAN, D.UNAVAILABLE, (Reason("no scanner"),)),
            GateDecision(GateKind.RUNTIME, D.UNAVAILABLE, (Reason("no runtime"),)),
            GateDecision(GateKind.A11Y, D.SKIPPED, (Reason("no ui"),)),
        ),
        context=ContextPlan((), (), 1, 0, 0),
        skills=(),
        evidence_level="elevated",
        policy=PolicyRef("default", "sha256:" + "0" * 64, waivers_permit_pass=True),
        generator={"name": "t", "version": "0"},
    )


def collect_gh(repo: GitRepo, gh: FakeGitHub, tmp_path: Path, **extra: str):
    env = github_env({}, gh.url, write_event(tmp_path / "event.json"), **extra)
    return waivers.collect_report(repo.root, make_plan(repo), "github", env)


# --------------------------------------------------------------------------- github: labels


def test_label_by_codeowner_with_reason_is_a_waiver(repo, gh, tmp_path):
    gh.pr(
        events=[label("security_scan", OWNER)],
        comments=[comment(OWNER, "Looks fine.\nrepomarshal-waive security_scan: no scanner yet")],
    )
    found, notes = collect_gh(repo, gh, tmp_path)
    assert len(found) == 1, notes
    w = found[0]
    assert (w.target, w.identity, w.source) == ("security_scan", OWNER, WaiverSource.GITHUB_LABEL)
    assert w.reason == "no scanner yet"
    assert set(gh.auth) == {"Bearer test-token"}


def test_label_gate_id_target_by_path_owner(repo, gh, tmp_path):
    gh.pr(
        events=[label("backend:unit", BACKEND_OWNER)],
        comments=[comment(BACKEND_OWNER, "repomarshal-waive backend:unit: flaky infra")],
    )
    found, notes = collect_gh(repo, gh, tmp_path)
    assert [(w.target, w.reason) for w in found] == [("backend:unit", "flaky infra")], notes


def test_label_by_admin_without_codeowner_entry(repo, gh, tmp_path):
    gh.pr(
        events=[label("runtime", ADMIN)],
        comments=[comment(ADMIN, "repomarshal-waive runtime: no device lab")],
        permissions={ADMIN: "admin"},
    )
    found, notes = collect_gh(repo, gh, tmp_path)
    assert [w.identity for w in found] == [ADMIN], notes


@pytest.mark.parametrize(
    "granter, permission",
    [(AUTHOR, "admin"), (BOT, "admin"), (OUTSIDER, "write"), (OUTSIDER, "403")],
    ids=["pr-author", "bot", "non-owner-write", "non-owner-403"],
)
def test_label_by_untrusted_account_is_no_waiver(repo, gh, tmp_path, granter, permission):
    gh.pr(
        events=[label("runtime", granter)],
        comments=[comment(granter, "repomarshal-waive runtime: trust me")],
        permissions={granter: permission},
    )
    found, notes = collect_gh(repo, gh, tmp_path)
    assert found == (), notes
    assert notes


def test_backend_owner_cannot_waive_kind_level_target_outside_star_rule(repo, gh, tmp_path):
    # be-owner owns backend/ and the change is only backend/app.py: kind-level security_scan
    # covers the same paths, so be-owner may waive it; a docs-team member could not.
    gh.pr(
        events=[label("security_scan", BACKEND_OWNER)],
        comments=[comment(BACKEND_OWNER, "repomarshal-waive security_scan: reviewed by hand")],
    )
    found, _ = collect_gh(repo, gh, tmp_path)
    assert [w.identity for w in found] == [BACKEND_OWNER]


def test_label_without_reason_is_no_waiver(repo, gh, tmp_path):
    gh.pr(
        events=[label("runtime", OWNER)],
        comments=[
            comment(OWNER, "please waive runtime"),
            comment(OWNER, "repomarshal-waive runtime:   "),
            comment(AUTHOR, "repomarshal-waive runtime: the agent writes the reason"),
        ],
    )
    found, notes = collect_gh(repo, gh, tmp_path)
    assert found == ()
    assert any("no PR comment" in n for n in notes)


def test_label_removed_then_reapplied_by_author_is_no_waiver(repo, gh, tmp_path):
    gh.pr(
        events=[
            label("runtime", OWNER, n=1),
            label("runtime", OWNER, "unlabeled", n=2),
            label("runtime", AUTHOR, n=3),
        ],
        comments=[comment(OWNER, "repomarshal-waive runtime: ok")],
    )
    found, _ = collect_gh(repo, gh, tmp_path)
    assert found == ()


def test_label_removed_is_no_waiver(repo, gh, tmp_path):
    gh.pr(
        events=[label("runtime", OWNER, n=1), label("runtime", OWNER, "unlabeled", n=2)],
        comments=[comment(OWNER, "repomarshal-waive runtime: ok")],
    )
    assert collect_gh(repo, gh, tmp_path)[0] == ()


def test_label_for_target_not_in_plan_is_ignored(repo, gh, tmp_path):
    gh.pr(
        events=[label("a11y", OWNER), label("nope:unit", OWNER, n=2)],
        comments=[comment(OWNER, "repomarshal-waive a11y: x\nrepomarshal-waive nope:unit: y")],
    )
    assert collect_gh(repo, gh, tmp_path)[0] == ()


# --------------------------------------------------------------------------- github: reviews


def test_approving_codeowner_review_is_not_a_waiver(repo, gh, tmp_path):
    """v0.4: the reviews API never says whether a review was made through a GitHub App, so an
    approving CODEOWNER review cannot be shown to be a person's: rejected, with a note."""
    gh.pr(
        reviews=[
            review(
                OWNER,
                "APPROVED",
                "LGTM\nrepomarshal-waive runtime: no device lab\n"
                "repomarshal-waive security_scan: scanned by hand",
            )
        ]
    )
    found, notes = collect_gh(repo, gh, tmp_path)
    assert found == (), notes
    assert any("reviews API does not say" in n for n in notes)


def test_commented_review_is_no_waiver(repo, gh, tmp_path):
    gh.pr(reviews=[review(OWNER, "COMMENTED", "repomarshal-waive runtime: no device lab")])
    found, notes = collect_gh(repo, gh, tmp_path)
    assert found == ()
    assert any("not an approval" in n for n in notes)


def test_latest_review_per_user_wins(repo, gh, tmp_path):
    gh.pr(
        reviews=[
            review(OWNER, "APPROVED", "repomarshal-waive runtime: ok"),
            review(OWNER, "CHANGES_REQUESTED", "repomarshal-waive runtime: ok"),
        ]
    )
    assert collect_gh(repo, gh, tmp_path)[0] == ()


@pytest.mark.parametrize(
    "rv",
    [
        review(OUTSIDER, "APPROVED", "repomarshal-waive runtime: ok"),
        review(BOT, "APPROVED", "repomarshal-waive runtime: ok"),
        review(AUTHOR, "APPROVED", "repomarshal-waive runtime: ok"),
        review(OWNER, "APPROVED", "repomarshal-waive runtime: ok", commit="1" * 40),
    ],
    ids=["non-owner", "bot", "author", "stale-commit"],
)
def test_untrusted_or_stale_review_is_no_waiver(repo, gh, tmp_path, rv):
    gh.pr(reviews=[rv], permissions={OUTSIDER: "admin"})
    assert collect_gh(repo, gh, tmp_path)[0] == ()


# --------------------------------------------------------------------------- github: fail closed


def test_api_500_is_no_waivers(repo, gh, tmp_path):
    gh.pr(
        events=[label("runtime", OWNER)],
        comments=[comment(OWNER, "repomarshal-waive runtime: ok")],
        reviews=[review(OWNER, "APPROVED", "repomarshal-waive security_scan: ok")],
    )
    gh.routes["/repos/acme/webapp/pulls/7/reviews"] = (500, {"message": "boom"})
    found, notes = collect_gh(repo, gh, tmp_path)
    assert found == ()  # the label waiver is dropped too: all or nothing
    assert any("HTTP 500" in n for n in notes)


def test_api_timeout_is_no_waivers(repo, gh, tmp_path, monkeypatch):
    monkeypatch.setattr(waivers, "TIMEOUT_S", 0.3)
    gh.routes["/repos/acme/webapp/issues/7/events"] = "timeout"
    found, notes = collect_gh(repo, gh, tmp_path)
    assert found == ()
    assert any("fail closed" in n for n in notes)


def test_unreachable_api_is_no_waivers(repo, tmp_path):
    env = github_env({}, "http://127.0.0.1:9", write_event(tmp_path / "e.json"))
    found, notes = waivers.collect_report(repo.root, make_plan(repo), "github", env)
    assert found == () and notes


def test_collect_never_raises(repo, gh, tmp_path, capsys):
    gh.routes["/repos/acme/webapp/issues/7/events"] = {"not": "a list"}
    env = github_env({}, gh.url, write_event(tmp_path / "e.json"))
    assert waivers.collect(repo.root, make_plan(repo), "github", env) == ()
    assert "repomarshal waivers:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "override",
    [
        {"GITHUB_ACTIONS": "false"},
        {"GITHUB_EVENT_NAME": "push"},
        {"GITHUB_TOKEN": ""},
        {"GITHUB_API_URL": "file:///etc"},
        {"GITHUB_EVENT_PATH": "/nonexistent/event.json"},
    ],
)
def test_github_mode_needs_a_pr_event_in_actions(repo, gh, tmp_path, override):
    gh.pr(
        events=[label("runtime", OWNER)], comments=[comment(OWNER, "repomarshal-waive runtime: x")]
    )
    found, notes = collect_gh(repo, gh, tmp_path, **override)
    assert found == () and notes
    assert gh.requests == []


def test_pull_request_review_event_is_accepted(repo, gh, tmp_path):
    gh.pr(
        events=[label("runtime", OWNER)],
        comments=[comment(OWNER, "repomarshal-waive runtime: ok")],
    )
    found, _ = collect_gh(repo, gh, tmp_path, GITHUB_EVENT_NAME="pull_request_review")
    assert [w.target for w in found] == ["runtime"]


def test_redirect_is_not_followed(repo, gh, tmp_path):
    gh.routes["/repos/acme/webapp/issues/7/events"] = (302, {})
    assert collect_gh(repo, gh, tmp_path)[0] == ()


def test_local_files_ignored_in_github_mode(repo, gh, tmp_path):
    plan = make_plan(repo)
    _write_local(repo.root, "runtime", plan.change.tree_digest)
    gh.pr()
    env = github_env({}, gh.url, write_event(tmp_path / "e.json"))
    found, _ = waivers.collect_report(repo.root, plan, "github", env)
    assert found == ()
    assert waivers.collect_report(repo.root, plan, "local", {})[0] != ()


# --------------------------------------------------------------------------- CODEOWNERS


def test_codeowners_last_match_wins_and_teams_ignored():
    co = waivers.CodeOwners(
        "# comment\n* @Root\n*.py @py-owner\n/docs/ @org/team @doc-owner\n"
        "src/**/gen/ @gen # trailing comment @nobody\n"
    )
    assert co.everyone == {"root"}
    assert co.owners_of("a/b.py") == {"py-owner"}
    assert co.owners_of("docs/x.md") == {"doc-owner"}
    assert co.owners_of("src/a/b/gen/x.txt") == {"gen"}
    assert co.owners_of("README.md") == {"root"}
    assert co.owns("ROOT", ())
    assert co.owns("py-owner", ("a.py", "b/c.py"))
    assert not co.owns("py-owner", ("a.py", "README.md"))
    assert not co.owns("doc-owner", ())


def test_codeowners_read_at_base_not_worktree(repo):
    repo.write(".github/CODEOWNERS", f"* @{AUTHOR}\n")  # the change edits CODEOWNERS
    co = waivers.CodeOwners.at_base(repo.root, repo.head())
    assert co.everyone == {OWNER}
    assert waivers.CodeOwners.at_base(repo.root, None).rules == []


# --------------------------------------------------------------------------- local mode


def _write_local(root: Path, target: str, tree: str, **over: Any) -> Path:
    d = root / ".repomarshal" / "waivers"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "repomarshal.waiver/1",
        "target": target,
        "reason": "hardware unavailable",
        "identity": "human@example.invalid (os user human)",
        "source": "local-cli",
        "granted_at": "2026-10-13T00:00:00Z",
        "tree_digest": tree,
        **over,
    }
    p = d / f"20261013T000000Z-{target.replace(':', '_')}-{len(list(d.iterdir()))}.json"
    p.write_text(json.dumps(data))
    return p


def test_local_waiver_bound_to_tree_and_plan(repo):
    plan = make_plan(repo)
    tree = plan.change.tree_digest
    _write_local(repo.root, "runtime", tree)
    _write_local(repo.root, "security_scan", "other-tree")
    _write_local(repo.root, "a11y", tree)  # SKIPPED: not waivable
    _write_local(repo.root, "nope:unit", tree)
    _write_local(repo.root, "backend:unit", tree, reason="  ")
    _write_local(repo.root, "backend:unit", tree, source="github-label")
    (repo.root / ".repomarshal/waivers/broken.json").write_text("{not json")
    found, notes = waivers.collect_report(repo.root, plan, "local", {})
    assert [(w.target, w.source, w.tree_digest) for w in found] == [
        ("runtime", WaiverSource.LOCAL_CLI, tree)
    ]
    assert found[0].reference.startswith(".repomarshal/waivers/")
    assert len(notes) == 6


@pytest.mark.parametrize("ci", [{"CI": "true"}, {"GITHUB_ACTIONS": "true"}])
def test_local_waivers_never_used_in_ci(repo, ci):
    plan = make_plan(repo)
    _write_local(repo.root, "runtime", plan.change.tree_digest)
    found, notes = waivers.collect_report(repo.root, plan, "local", ci)
    assert found == () and notes


def test_mode_none_and_unknown_mode(repo):
    plan = make_plan(repo)
    _write_local(repo.root, "runtime", plan.change.tree_digest)
    assert waivers.collect_report(repo.root, plan, "none", {}) == ((), ())
    found, notes = waivers.collect_report(repo.root, plan, "bogus", {})
    assert found == () and notes


def test_waive_refuses_without_a_tty(repo):
    env = {"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"}
    r = subprocess.run(
        [sys.executable, "-m", "repomarshal", "waive", "runtime", "--reason", "x"],
        cwd=repo.root,
        env=env,
        input="runtime\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1
    assert "interactive terminal" in r.stderr
    assert not (repo.root / ".repomarshal" / "waivers").exists()


def test_waive_refuses_when_only_stdin_is_a_tty(repo, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    args = argparse.Namespace(gate="runtime", reason="x", repo=str(repo.root))
    assert waivers.cli_waive(args) == 1
    assert "TTY" in capsys.readouterr().err
