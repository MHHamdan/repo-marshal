"""In-process attacks on `repomarshal.ci.trusted_verdict` (v0.5, threat T31).

The attacker is a PR author or a coding agent that can push branches and open PRs (through a
GitHub App under the maintainer's account, without admin rights). The gate jobs are simulated as
in tests/unit/test_ci_trusted.py: one fresh clone per `rm-gate-N` job, GitHub's step record as a
dict, and the GitHub REST API as a FakeGitHub.

A "forged" gate job is what a `ci.yml` the attacker controls can produce: every `rm-gate-N` job
records `run gate` and `gate passed` as success and uploads a result file that binds to the plan
(same gate, plan hash, HEAD and tree, all computable with the BASE judge) but says PASS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repomarshal import ci
from repomarshal.model import Verdict
from tests.unit.test_ci_split import PR, record
from tests.unit.test_ci_trusted import ATTEMPT, REPO, RUN_ID, run_gates
from tests.unit.test_waivers import FakeGitHub, comment, label

_clones = iter(range(10_000))


@pytest.fixture
def gh(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    with FakeGitHub() as fake:
        yield fake


def serve(gh: FakeGitHub, pr: PR, base_ref: str = "main") -> None:
    """The triggering `ci` run, the PR (with its base branch name) and the repository."""
    head = pr.pr_head
    gh.routes[f"/repos/{REPO}"] = {"full_name": REPO, "default_branch": "main"}
    gh.routes[f"/repos/{REPO}/actions/runs/{RUN_ID}"] = {
        "id": int(RUN_ID),
        "path": ci.WORKFLOW_PATH,
        "event": "pull_request",
        "head_sha": head,
        "run_attempt": int(ATTEMPT),
    }
    pull = {
        "number": 3,
        "state": "open",
        "user": {"login": "MHHamdan", "type": "User"},
        "head": {"sha": head, "ref": "feature"},
        "base": {"sha": pr.base, "ref": base_ref, "repo": {"full_name": REPO}},
    }
    gh.routes[f"/repos/{REPO}/commits/{head}/pulls"] = [pull]
    gh.routes[f"/repos/{REPO}/pulls/3"] = pull
    # Since the fix for #3/#4 the judge also asks whether a newer ci run supersedes this one.
    gh.routes[f"/repos/{REPO}/actions/workflows/ci.yml/runs"] = {
        "workflow_runs": [gh.routes[f"/repos/{REPO}/actions/runs/{RUN_ID}"]]
    }


def forge_pass(results: Path, jobs: dict) -> None:
    """What gate jobs defined by an attacker's ci.yml report: PASS everywhere, bound files."""
    for name in list(jobs):
        jobs[name] = record(0)
    for path in results.glob("rm-gate-*/result.json"):
        doc = json.loads(path.read_text())
        doc["result"].update(status="PASS", exit_code=0, reason="forged by the PR's ci.yml")
        path.write_text(json.dumps(doc))


def trusted(pr: PR, gh: FakeGitHub, results: Path, jobs: dict, waivers: str = "none"):
    env = {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": "t",  # noqa: S105 - fake
        "GITHUB_API_URL": gh.url,
        # What a workflow_run job on the default branch sees.
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
        "PATH": pr.env()["PATH"],
        "HOME": pr.env()["HOME"],
    }
    return ci.trusted_verdict(
        pr.clone(f"trusted-{next(_clones)}"), RUN_ID, ATTEMPT, pr.pr_head, results, env,
        waivers=waivers, jobs=jobs,
    )  # fmt: skip


def _retarget_event(pr: PR) -> None:
    pr.event.write_text(json.dumps({"pull_request": {"number": 3, "head": {"sha": pr.pr_head}}}))


# --------------------------------------------------------------------------- base branch


@pytest.mark.failopen
def test_pr_against_an_attacker_branch_is_not_judged_by_that_branch(tmp_path, gh):
    """FAIL-OPEN (critical). The trusted verdict accepts ANY base branch of the PR. The attacker
    pushes a branch `evil` (from main) whose ci.yml makes every rm-gate job report PASS, bases
    the PR's branch on it and opens the PR against `evil`. The PR itself does not touch ci.yml
    relative to its merge base (the tip of `evil`), so the ci-workflow check sees nothing, and
    the forged gate records bind. The status `repomarshal/verdict: success` lands on the head
    SHA; statuses belong to the commit, so retargeting the PR to main (an `edited` event, which
    does not re-run `ci`) keeps it. The same base choice also selects the judge's own code
    (installed from the base commit) and the BASE policy.

    Secure: only a PR whose base is the default branch (or an allow-listed protected branch)
    can get a PASS from the trusted workflow."""
    pr = PR(tmp_path, unit="1")  # the PR's tests genuinely FAIL
    r = pr.r
    r.git("checkout", "-q", "main")
    r.git("checkout", "-q", "-b", "evil")
    r.write(".github/workflows/ci.yml", "name: ci\n# attacker: every rm-gate job reports PASS\n")
    evil = r.commit("evil: forged gate jobs")
    r.git("checkout", "-q", "pr")
    r.git("merge", "-q", "--no-edit", "evil")
    pr.pr_head = pr.checkout = r.head()
    pr.base = evil
    _retarget_event(pr)
    serve(gh, pr, base_ref="evil")
    results, jobs = run_gates(pr)
    assert jobs and any(j[ci.STEP_RUN] == "failure" for j in jobs.values())  # genuine FAIL
    forge_pass(results, jobs)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is not Verdict.PASS, res.reasons
    assert ci.commit_status(res)[0] != "success"


def test_same_forgery_against_main_is_refused(tmp_path, gh):
    """Defended: against main, the PR has to edit ci.yml itself to forge the gate records, and
    the ci-workflow check refuses them (UNKNOWN, status `error`)."""
    pr = PR(tmp_path, unit="1")
    pr.r.write(".github/workflows/ci.yml", "name: ci\n# attacker: every rm-gate job reports PASS\n")
    pr.pr_head = pr.checkout = pr.r.commit("PR edits ci.yml")
    _retarget_event(pr)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    forge_pass(results, jobs)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN
    assert ci.commit_status(res)[0] == "error"


# --------------------------------------------------------------------------- ci-workflow waiver


def _workflow_pr(tmp_path: Path, monkeypatch) -> PR:
    import tests.unit.test_ci_split as split

    extra = "[completion]\nwaivers_permit_pass = true\nallow_author_waivers = true\n"
    monkeypatch.setattr(split, "POLICY", split.POLICY + extra)
    pr = PR(tmp_path)
    pr.r.git("checkout", "-q", "main")
    pr.r.write(".github/CODEOWNERS", "* @MHHamdan\n")
    pr.base = pr.r.commit("BASE: CODEOWNERS")
    pr.r.git("checkout", "-q", "pr")
    pr.r.git("merge", "-q", "--no-edit", "main")
    pr.r.write(".github/workflows/ci.yml", "name: ci\n# reviewed change: add a cache step\n")
    pr.pr_head = pr.checkout = pr.r.commit("PR edits ci.yml (reviewed)")
    return pr


@pytest.mark.failopen
def test_ci_workflow_waiver_does_not_cover_a_later_rewrite_of_ci_yml(tmp_path, gh, monkeypatch):
    """FAIL-OPEN (high). The maintainer reviews the PR's ci.yml change at commit X1 and grants
    `repomarshal-waive:ci-workflow` with a reason comment. The agent then pushes X2, which
    rewrites ci.yml so that every rm-gate job reports PASS, and breaks the tests. The label is
    still on the PR and nothing ties it to the ci.yml that was reviewed, so the trusted verdict
    accepts the forged gate records: PASS for code whose tests fail.

    T33 (labels persist across pushes) is documented for gate waivers, where a stale waiver only
    lifts an UNKNOWN gate. The ci-workflow waiver lifts the binding of EVERY gate, so a stale one
    turns into a forged PASS. Secure: the waiver binds to the reviewed ci.yml (blob id or head
    SHA in the reason, or a label applied after the last push)."""
    pr = _workflow_pr(tmp_path, monkeypatch)
    reviewed = pr.pr_head
    gh.pr(
        events=[
            label(ci.WORKFLOW_WAIVER, "MHHamdan", n=1),
            label("integration", "MHHamdan", n=2),
        ],
        comments=[
            comment(
                "MHHamdan",
                f"repomarshal-waive {ci.WORKFLOW_WAIVER}: I reviewed the ci.yml diff\n"
                "repomarshal-waive integration: no integration env for CI-only changes",
            )
        ],
        repo=REPO,
        number=3,
    )
    # After the review: the agent rewrites ci.yml and the tests now fail.
    pr.r.write(".github/workflows/ci.yml", "name: ci\n# rewritten: every rm-gate job passes\n")
    pr.r.write("state.txt", "style=0 tests=1\n")
    pr.pr_head = pr.checkout = pr.r.commit("PR: rewrite ci.yml after review")
    assert pr.pr_head != reviewed
    gh.routes[f"/repos/{REPO}/issues/3/timeline"] = [
        {"event": "labeled", "created_at": "2026-10-02T00:00:00Z"},
        {"event": "committed", "sha": pr.pr_head},
    ]
    _retarget_event(pr)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    assert any(j[ci.STEP_RUN] == "failure" for j in jobs.values())  # genuine FAIL
    forge_pass(results, jobs)
    res = trusted(pr, gh, results, jobs, waivers="github")
    assert res.verdict is not Verdict.PASS, res.reasons
    assert ci.commit_status(res)[0] != "success"


def test_ci_workflow_waiver_by_the_agent_is_rejected_even_as_the_maintainer(
    tmp_path, gh, monkeypatch
):
    """Defended: the agent acts through an app under the maintainer's login. Its label and
    comment carry performed_via_github_app, so it cannot grant ci-workflow to itself, even with
    allow_author_waivers."""
    pr = _workflow_pr(tmp_path, monkeypatch)
    pr.r.write("state.txt", "style=0 tests=1\n")
    pr.pr_head = pr.checkout = pr.r.commit("PR: tests fail")
    app = {"performed_via_github_app": {"slug": "claude"}}
    gh.pr(
        events=[
            label(ci.WORKFLOW_WAIVER, "MHHamdan", n=1, via=app),
            label("integration", "MHHamdan", n=2, via=app),
        ],
        comments=[
            comment(
                "MHHamdan",
                f"repomarshal-waive {ci.WORKFLOW_WAIVER}: ok\nrepomarshal-waive integration: ok",
                via=app,
            )
        ],
        repo=REPO,
        number=3,
    )
    _retarget_event(pr)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    forge_pass(results, jobs)
    res = trusted(pr, gh, results, jobs, waivers="github")
    assert res.verdict is Verdict.UNKNOWN
    assert any(ci.WORKFLOW_WAIVER in r for r in res.reasons)


def test_ci_workflow_label_is_not_a_target_when_ci_yml_is_unchanged(tmp_path, gh, monkeypatch):
    """Defended: a genuine ci-workflow waiver cannot lift anything when the PR does not change
    ci.yml (it is no gate target), so it cannot be repurposed to waive a gate."""
    import tests.unit.test_ci_split as split

    extra = "[completion]\nwaivers_permit_pass = true\nallow_author_waivers = true\n"
    monkeypatch.setattr(split, "POLICY", split.POLICY + extra)
    pr = PR(tmp_path)
    gh.pr(
        events=[label(ci.WORKFLOW_WAIVER, "MHHamdan", n=1)],
        comments=[comment("MHHamdan", f"repomarshal-waive {ci.WORKFLOW_WAIVER}: fine")],
        repo=REPO,
        number=3,
    )
    serve(gh, pr)
    results, jobs = run_gates(pr)
    jobs["rm-gate-1"] = {ci.STEP_RUN: "skipped", ci.STEP_PASSED: "skipped"}
    res = trusted(pr, gh, results, jobs, waivers="github")
    assert res.verdict is Verdict.UNKNOWN
    assert not any(w.target == ci.WORKFLOW_WAIVER for w in res.record.waivers)


# --------------------------------------------------------------------------- judge output


def test_judge_output_cannot_carry_workflow_commands(tmp_path, gh, monkeypatch, capsys):
    """Defended: the judge's JSON is printed into the trusted job's log (`cat`), where the runner
    parses `::command::` lines. PR-controlled text (a gate's reason and output tail, copied from
    the uploaded result file) is JSON-escaped and indented, so no output line starts with `::`,
    and GITHUB_OUTPUT gets only the fixed state/description."""
    from repomarshal import cli

    pr = PR(tmp_path)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    evil = "x\n::add-mask::ghs_x\n::set-output name=state::success\nstate=success"
    for path in results.glob("rm-gate-*/result.json"):
        doc = json.loads(path.read_text())
        doc["result"]["reason"] = evil
        doc["result"]["output_tail"] = evil.splitlines() + ["::stop-commands::t"]
        path.write_text(json.dumps(doc))
    monkeypatch.setattr(ci, "fetch_job_steps", lambda env: (jobs, None))
    out = tmp_path / "gh_out"
    for k, v in {
        "GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t", "GITHUB_API_URL": gh.url,
        "GITHUB_OUTPUT": str(out),
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    code = cli.main(
        ["ci", "trusted-verdict", "--repo", str(pr.clone("t")), "--run-id", RUN_ID,
         "--run-attempt", ATTEMPT, "--head-sha", pr.pr_head, "--results", str(results),
         "--waivers", "none", "--github-output", "--json"]
    )  # fmt: skip
    printed = capsys.readouterr().out
    assert code == 0 and "add-mask" in printed  # the text did reach the output
    assert not [ln for ln in printed.splitlines() if ln.lstrip().startswith("::")]
    lines = out.read_text().splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == ["state", "description"]
