"""Waivers (v0.3): an agent must never be able to grant one, and a waiver must never turn
"nothing was verified" into PASS.

Threat: the agent (or the PR's own code, which the CI gates execute in the same job as the
judge) controls the working tree, every file the runner user can write, and the commits of the
PR. Waivers in CI come only from GitHub (labels + comments, CODEOWNER reviews on the current
head), CODEOWNERS and the policy switch are read at BASE.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from helpers import GitRepo
from repomarshal import api, hooks, verdict, waivers
from repomarshal.model import (
    ChangeSet,
    ContextPlan,
    DecisionStatus,
    GateDecision,
    GateKind,
    GateResult,
    Plan,
    PlanMode,
    PolicyRef,
    Reason,
    ResultStatus,
    Risk,
    Verdict,
    Waiver,
    WaiverSource,
)
from tests.unit.test_waivers import (
    AUTHOR,
    OWNER,
    FakeGitHub,
    comment,
    github_env,
    label,
    review,
    write_event,
)

NEW_HEAD = "b" * 40  # the PR head the CI run is judging
OLD_HEAD = "a" * 40  # an earlier push that a CODEOWNER approved (with a waiver line)
OTHER_PR = 8  # another PR in the same repository with a genuine maintainer waiver


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        yield fake


def _clean_env(**extra: str) -> dict[str, str]:
    """os.environ without CI variables, no bytecode writes (they would change the tree)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_") and k not in ("CI",)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


# --------------------------------------------------------------------------- W1: event payload


POLICY_WITH_GATES = """version = 1
[completion]
waivers_permit_pass = true
[gates.pytests]
kind = "unit"
argv = [{python}, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
[gates.style]
kind = "lint"
argv = ["repomarshal-review-no-such-linter"]
"""

# PR code: an ordinary-looking test that rewrites the Actions event payload the judge reads
# AFTER the gates ran. Nothing in the working tree changes, so the tree digest is unaffected.
EVIL_TEST = """import json, os


def test_tamper():
    path = os.environ.get("GITHUB_EVENT_PATH")
    if path:
        with open(path) as fh:
            data = json.load(fh)
        data["pull_request"].update({updates})
        with open(path, "w") as fh:
            json.dump(data, fh)
"""


def _ci_repo(tmp_path: Path, updates: str) -> tuple[GitRepo, str]:
    r = GitRepo(tmp_path / "repo")
    r.write("repomarshal.toml", POLICY_WITH_GATES.format(python=json.dumps(sys.executable)))
    r.write(".github/CODEOWNERS", f"* @{OWNER}\n")
    r.write("conftest.py", "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n")
    r.write("app.py", "def add(a, b):\n    return a + b\n")
    r.write(
        "tests/test_app.py", "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    base = r.commit("BASE")
    # The PR: a code change (unit + lint required; the linter is not installed in CI, so the
    # `style` gate is UNAVAILABLE and needs a waiver) plus the tampering test.
    r.write("app.py", "def add(a, b):\n    return b + a\n")
    r.write("tests/test_tamper.py", EVIL_TEST.format(updates=updates))
    return r, base


def _seed_github(gh: FakeGitHub) -> None:
    # PR 7 (this one): the CODEOWNER approved an OLDER head with a waiver line; the agent pushed
    # again since, so the review must not count.
    gh.pr(
        reviews=[
            review(
                OWNER,
                "APPROVED",
                "repomarshal-waive style: linter licence expired",
                commit=OLD_HEAD,
            )
        ]
    )
    # PR 8 (another PR): a genuine maintainer label + comment waiver for `style`.
    gh.pr(
        number=OTHER_PR,
        events=[label("style", OWNER)],
        comments=[comment(OWNER, "repomarshal-waive style: linter licence expired")],
    )


@pytest.mark.failopen
@pytest.mark.parametrize(
    "updates",
    [
        pytest.param(f'{{"head": {{"sha": "{OLD_HEAD}"}}}}', id="stale-review-revived"),
        pytest.param(f'{{"number": {OTHER_PR}}}', id="other-prs-waiver-borrowed"),
    ],
)
def test_gate_code_cannot_rewrite_the_event_payload_the_judge_trusts(gh, tmp_path, updates):
    """W1 (CI). `_collect_github` reads GITHUB_EVENT_PATH (PR number, author, head sha) only
    AFTER api.done has executed every gate, and the gates run the PR's own code as the same user
    that owns the event file. A test in the PR rewrites `pull_request.head.sha` to the commit an
    old CODEOWNER approval was made on (or `number` to another PR with a genuine waiver), and
    the stale/foreign waiver turns UNKNOWN into PASS. The honest verdict is UNKNOWN."""
    _seed_github(gh)
    r, base = _ci_repo(tmp_path, updates)
    event = write_event(tmp_path / "event.json", author=AUTHOR, head=NEW_HEAD)
    env = github_env(_clean_env(), gh.url, event)

    honest = waivers.collect_report(r.root, api.plan(r.root, base=base), "github", env)[0]
    assert honest == (), "precondition: no genuine waiver for this PR's current head"

    res = api.done(r.root, base=base, env=env, waivers="github")
    assert res.verdict is not Verdict.PASS, res.reasons


# --------------------------------------------------------------------------- W2: remembered BASE


def _policy(permit: bool) -> str:
    return f"version = 1\n[completion]\nwaivers_permit_pass = {'true' if permit else 'false'}\n"


@pytest.mark.failopen
def test_pr_committed_base_state_cannot_move_the_ci_base_to_an_older_policy(tmp_path):
    """W2 (CI). anchor.effective_base widens an explicit --base to merge-base(explicit,
    remembered), and the remembered BASE comes from `.repomarshal/state/base.json` in the
    CHECKOUT. A PR can force-add that file (`git add -f`; evidence paths are even dropped from
    the change set, so it is invisible in the plan). In CI the checkout is detached, so the key
    is "(detached)". The judge then reads policy, CODEOWNERS and gate definitions from an
    ancestor the PR chose: here one where waivers still counted as PASS, although BASE turned
    them off. The CI-provided BASE must not be moved by repository content."""
    r = GitRepo(tmp_path / "repo")
    r.write("repomarshal.toml", _policy(True))
    r.write(".github/CODEOWNERS", "* @former-maintainer\n")
    r.write("README.md", "# demo\n")
    old = r.commit("A: waivers allowed")
    r.write("repomarshal.toml", _policy(False))
    r.write(".github/CODEOWNERS", f"* @{OWNER}\n")
    base = r.commit("B: waivers no longer count (BASE of the PR)")
    r.git("switch", "-q", "-c", "pr")
    r.write("app.py", "x = 1\n")
    r.write(".repomarshal/state/base.json", json.dumps({"(detached)": old, "pr": old}))
    r.git("add", "app.py")
    r.git("add", "-f", ".repomarshal/state/base.json")
    r.commit("PR", add_all=False)
    r.git("checkout", "-q", "--detach")  # actions/checkout of the PR merge ref

    p = api.plan(r.root, base=base)
    assert p.change.base == base, f"BASE moved to the PR-chosen ancestor {p.change.base[:12]}"
    assert p.policy.waivers_permit_pass is False


# --------------------------------------------------------------------------- W3: nothing verified


def _write_local_waiver(root: Path, target: str, tree: str) -> None:
    d = root / ".repomarshal" / "waivers"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"20261014T000000Z-{target}.json").write_text(
        json.dumps(
            {
                "schema": waivers.LOCAL_SCHEMA,
                "target": target,
                "reason": "no test runner on this project yet",
                "identity": "human@example.invalid (os user human)",
                "source": "local-cli",
                "granted_at": "2026-10-14T00:00:00Z",
                "tree_digest": tree,
            }
        )
    )


@pytest.mark.failopen
def test_waiver_cannot_turn_a_plan_with_no_executed_gate_into_pass(tmp_path):
    """W3. waivers.md: "Never waivable: ... 'nothing was verified' (a plan with no required
    gates)". verdict.fold counts an UNAVAILABLE decision in `counted`, so a code change in a
    project with no test tooling (the only counted decision is `unit`, UNAVAILABLE) plus one
    waiver for `unit` yields PASS although not a single gate executed: zero evidence. (The same
    waiver arrives in CI from a maintainer label; local mode is used here to stay offline.)"""
    r = GitRepo(tmp_path / "repo")
    r.write("repomarshal.toml", _policy(True))
    r.write("README.md", "# demo\n")
    base = r.commit("BASE")
    r.write("app.py", "def handler():\n    return 1\n")
    p = api.plan(r.root, base=base)
    counted = [
        d for d in p.gates if d.status in (DecisionStatus.REQUIRED, DecisionStatus.UNAVAILABLE)
    ]
    assert [(d.kind, d.status, d.gate_id) for d in counted] == [
        (GateKind.UNIT, DecisionStatus.UNAVAILABLE, None)
    ], "precondition: nothing in the plan can execute"
    _write_local_waiver(r.root, "unit", p.change.tree_digest)

    res = api.done(r.root, base=base, env=_clean_env(), waivers="local")
    executed_pass = [g for g in res.record.results if g.status is ResultStatus.PASS]
    assert executed_pass == []
    assert res.verdict is not Verdict.PASS, res.reasons


def _plan(*decisions: GateDecision, permit: bool = True) -> Plan:
    return Plan(
        mode=PlanMode.BINDING,
        task=None,
        change=ChangeSet(None, None, "0" * 40, (), False),
        signals=(),
        domains=(),
        risk=Risk.LOW,
        inventory=(),
        gates=tuple(decisions),
        context=ContextPlan((), (), 1, 0, 0),
        skills=(),
        evidence_level="standard",
        policy=PolicyRef("default", "sha256:" + "0" * 64, waivers_permit_pass=permit),
        generator={"name": "t", "version": "0"},
    )


def _waiver(target: str) -> Waiver:
    return Waiver(target, "accepted", "maint", WaiverSource.GITHUB_LABEL, "t")


@pytest.mark.failopen
def test_fold_all_counted_decisions_waived_is_not_pass():
    """W3, unit level: every counted decision waived, none executed -> PASS today."""
    p = _plan(
        GateDecision(GateKind.UNIT, DecisionStatus.UNAVAILABLE, (Reason("no runner"),)),
        GateDecision(GateKind.LINT, DecisionStatus.REQUIRED, (Reason("changed"),), "style"),
    )
    results = (GateResult("style", GateKind.LINT, ResultStatus.UNKNOWN, "unavailable"),)
    v, reasons = verdict.fold(p, results, False, True, (_waiver("unit"), _waiver("style")))
    assert v is not Verdict.PASS, reasons


# --------------------------------------------------------------------------- defended


def test_defended_fail_is_not_waivable_and_kind_target_does_not_cover_a_gate():
    """Defended: a FAIL stays FAIL with a waiver for it; a kind-level waiver (`lint`) does not
    match the gate-level decision `style` of the same kind."""
    p = _plan(
        GateDecision(GateKind.UNIT, DecisionStatus.REQUIRED, (Reason("c"),), "pytests"),
        GateDecision(GateKind.LINT, DecisionStatus.REQUIRED, (Reason("c"),), "style"),
    )
    results = (
        GateResult("pytests", GateKind.UNIT, ResultStatus.FAIL, "exit 1"),
        GateResult("style", GateKind.LINT, ResultStatus.UNKNOWN, "unavailable"),
    )
    assert verdict.fold(p, results, False, True, (_waiver("pytests"),))[0] is Verdict.FAIL
    ok = (GateResult("pytests", GateKind.UNIT, ResultStatus.PASS, "exit 0"), results[1])
    assert verdict.fold(p, ok, False, True, (_waiver("lint"),))[0] is Verdict.UNKNOWN
    assert verdict.fold(p, ok, False, True, (_waiver("style"),))[0] is Verdict.PASS


def test_defended_codeowners_edited_in_the_pr_is_ignored(gh, tmp_path):
    """Defended: the PR rewrites (and commits) CODEOWNERS to make its accomplice an owner; the
    label by that account is rejected because CODEOWNERS is read at BASE."""
    r = GitRepo(tmp_path / "repo")
    r.write(".github/CODEOWNERS", f"* @{OWNER}\n")
    r.write("README.md", "# x\n")
    base = r.commit("BASE")
    r.write(".github/CODEOWNERS", "* @accomplice\n")
    r.write("app.py", "x = 1\n")
    r.commit("PR")
    gh.pr(
        events=[label("unit", "accomplice")],
        comments=[comment("accomplice", "repomarshal-waive unit: fine")],
        permissions={"accomplice": "write"},
    )
    env = github_env({}, gh.url, write_event(tmp_path / "e.json"))
    found, notes = waivers.collect_report(r.root, api.plan(r.root, base=base), "github", env)
    assert found == (), notes


def test_defended_label_removed_on_a_later_page_is_no_waiver(monkeypatch, tmp_path):
    """Defended: pagination. The maintainer's `labeled` event is on page 1 (100 items) and the
    `unlabeled` on page 2; all pages are read, so the label is not considered applied. More
    than MAX_PAGES pages is an error (no waivers), never a silent truncation."""
    r = GitRepo(tmp_path / "repo")
    r.write("README.md", "# x\n")
    r.write(".github/CODEOWNERS", f"* @{OWNER}\n")
    base = r.commit("BASE")
    r.write("app.py", "x = 1\n")
    plan = api.plan(r.root, base=base)
    filler = [{"event": "commented", "actor": {"login": OWNER, "type": "User"}}] * 99
    pages: dict[str, list[list[dict[str, Any]]]] = {
        "/repos/acme/webapp/issues/7/events": [
            [label("unit", OWNER), *filler],
            [label("unit", OWNER, event="unlabeled", n=2)],
        ],
        "/repos/acme/webapp/issues/7/comments": [[comment(OWNER, "repomarshal-waive unit: ok")]],
        "/repos/acme/webapp/pulls/7/reviews": [[]],
    }

    def fake_get(self, path, params=None):
        page = int((params or {}).get("page", 1))
        listing = pages.get(path)
        if listing is None:
            raise waivers._ApiError(f"unexpected {path}")
        return listing[page - 1] if page <= len(listing) else []

    monkeypatch.setattr(waivers._GitHub, "get", fake_get)
    env = github_env({}, "https://api.github.invalid", write_event(tmp_path / "e.json"))
    found, notes = waivers.collect_report(r.root, plan, "github", env)
    assert found == (), notes

    pages["/repos/acme/webapp/issues/7/events"] = [[*filler, filler[0]]] * 30  # never ends
    found, notes = waivers.collect_report(r.root, plan, "github", env)
    assert found == () and any("pages" in n for n in notes)


def test_defended_hook_ignores_a_forged_local_waiver(tmp_path, monkeypatch, capsys):
    """Defended: the agent writes a perfectly valid local waiver file for the current tree; the
    Stop hook runs `done` with waivers "none", so the stop is still blocked."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    r = GitRepo(tmp_path / "repo")
    r.write("repomarshal.toml", _policy(True))
    r.write("README.md", "# demo\n")
    base = r.commit("BASE")
    r.write("app.py", "def handler():\n    return 1\n")
    _write_local_waiver(r.root, "unit", api.plan(r.root, base=base).change.tree_digest)
    payload = json.dumps(
        {
            "session_id": "s1",
            "cwd": str(r.root),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        }
    )
    code = hooks.run("claude-code", payload)
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)["decision"] == "block"
