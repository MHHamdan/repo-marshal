"""v0.5 trusted verdict (threat T31): `repomarshal ci trusted-verdict`, run by the default-branch
`workflow_run` workflow after the PR's `ci` run. The gate jobs run as in test_ci_split; the
GitHub API (workflow run, PR lookup, job records) is a FakeGitHub."""

from __future__ import annotations

import json

import pytest

from repomarshal import ci
from repomarshal.model import Verdict
from tests.unit.test_ci_split import PR, record, statuses
from tests.unit.test_waivers import FakeGitHub

REPO = "acme/webapp"
RUN_ID, ATTEMPT = "42", "1"


@pytest.fixture
def gh(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    with FakeGitHub() as fake:
        yield fake


def serve(gh: FakeGitHub, pr: PR, **run_overrides) -> None:
    head = pr.pr_head
    run = {
        "id": int(RUN_ID),
        "path": ci.WORKFLOW_PATH,
        "event": "pull_request",
        "head_sha": head,
        "run_attempt": int(ATTEMPT),
        **run_overrides,
    }
    gh.routes[f"/repos/{REPO}/actions/runs/{RUN_ID}"] = run
    pull = {
        "number": 3,
        "state": "open",
        "user": {"login": "MHHamdan", "type": "User"},
        "head": {"sha": head},
        "base": {"sha": pr.base, "repo": {"full_name": REPO}},
    }
    gh.routes[f"/repos/{REPO}/commits/{head}/pulls"] = [pull]
    gh.routes[f"/repos/{REPO}/pulls/3"] = pull


def run_gates(pr: PR):
    """The PR's ci run: plan job, then one fresh clone per gate job. Returns (results, jobs)."""
    planned = ci.plan_step(pr.clone("plan-job"), pr.base, pr.env(), waivers="none")
    results = pr.tmp / "results"
    jobs = {}
    for entry in planned["matrix"]:
        i, gate = entry["index"], entry["gate"]
        out = results / f"rm-gate-{i}" / "result.json"
        code = ci.gate_step(pr.clone(f"gate-{i}"), pr.base, gate, planned["expect"], out,
                            pr.env())  # fmt: skip
        jobs[f"rm-gate-{i}"] = record(code)
    return results, jobs


_clones = iter(range(10_000))


def trusted(pr: PR, gh: FakeGitHub, results, jobs, head=None, **kw):
    env = {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": "t",  # noqa: S105 - fake
        "GITHUB_API_URL": gh.url,
        "PATH": pr.env()["PATH"],
        "HOME": pr.env()["HOME"],
    }
    return ci.trusted_verdict(
        pr.clone(f"trusted-{next(_clones)}"), kw.pop("run_id", RUN_ID), kw.pop("attempt", ATTEMPT),
        head or pr.pr_head, results, env, waivers="none", jobs=jobs, **kw,
    )  # fmt: skip


def test_bound_run_gives_pass(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.PASS, res.reasons
    assert ci.commit_status(res)[0] == "success"


def test_failing_gate_gives_fail(tmp_path, gh):
    pr = PR(tmp_path, unit="1")
    serve(gh, pr)
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.FAIL
    assert ci.commit_status(res)[0] == "failure"


def test_pr_that_edits_ci_yml_is_unknown(tmp_path, gh):
    """The gate jobs are defined by ci.yml; a PR that edits it could make them say anything."""
    pr = PR(tmp_path)
    pr.r.write(".github/workflows/ci.yml", "name: ci\n# every gate job: exit 0\n")
    pr.pr_head = pr.checkout = pr.r.commit("PR edits ci.yml")
    serve(gh, pr)
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN
    assert set(statuses(res).values()) <= {"UNKNOWN", "PASS"}
    assert all(s == "UNKNOWN" for g, s in statuses(res).items() if not g.startswith("native:"))
    assert any(ci.WORKFLOW_PATH in r for r in res.reasons)
    assert ci.commit_status(res)[0] == "error"


def test_ci_yml_added_on_main_after_the_fork_is_not_a_pr_change(tmp_path, gh):
    """Only the PR's own changes count (merge base to head), not the base branch moving on."""
    pr = PR(tmp_path)
    pr.r.git("checkout", "-q", "main")
    pr.r.write(".github/workflows/ci.yml", "name: ci\n")
    pr.base = pr.r.commit("main: add ci.yml")
    pr.r.git("checkout", "-q", "pr")
    serve(gh, pr)
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert not any(ci.WORKFLOW_PATH in r for r in res.reasons), res.reasons


@pytest.mark.parametrize(
    "override",
    [
        {"path": ".github/workflows/other.yml"},
        {"event": "push"},
        {"head_sha": "f" * 40},
        {"run_attempt": 2},
    ],
    ids=["other-workflow", "push-event", "other-commit", "other-attempt"],
)
def test_triggering_run_must_be_the_prs_ci_run(tmp_path, gh, override):
    pr = PR(tmp_path)
    serve(gh, pr, **override)
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN, res.reasons
    assert all(s == "UNKNOWN" for g, s in statuses(res).items() if not g.startswith("native:"))


def test_no_open_pr_for_the_head_is_unknown(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    gh.routes[f"/repos/{REPO}/commits/{pr.pr_head}/pulls"] = []
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN and res.plan is None


def test_pr_from_another_repository_base_is_ignored(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    other = dict(gh.routes[f"/repos/{REPO}/commits/{pr.pr_head}/pulls"][0])
    other["base"] = {"sha": pr.base, "repo": {"full_name": "evil/fork"}}
    gh.routes[f"/repos/{REPO}/commits/{pr.pr_head}/pulls"] = [other]
    results, jobs = run_gates(pr)
    assert trusted(pr, gh, results, jobs).verdict is Verdict.UNKNOWN


def test_pr_head_moved_on_is_unknown(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    moved = dict(gh.routes[f"/repos/{REPO}/pulls/3"], head={"sha": "e" * 40})
    gh.routes[f"/repos/{REPO}/pulls/3"] = moved
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN
    assert any("moved on" in r for r in res.reasons)


def test_missing_or_skipped_gate_jobs_are_unknown(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    skipped = {"rm-gate-${{ matrix.index }}": {}}  # what GitHub records for a skipped matrix
    res = trusted(pr, gh, results, skipped)
    assert res.verdict is Verdict.UNKNOWN
    jobs["rm-gate-1"] = {ci.STEP_RUN: "skipped", ci.STEP_PASSED: "skipped",
                         ci.STEP_UPLOAD: "skipped"}  # fmt: skip
    res = trusted(pr, gh, results, jobs)
    assert statuses(res)["tests"] == "UNKNOWN" and res.verdict is Verdict.UNKNOWN


def test_api_error_is_unknown(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    gh.routes[f"/repos/{REPO}/actions/runs/{RUN_ID}"] = (500, {})
    results, jobs = run_gates(pr)
    res = trusted(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN and res.plan is None


def test_job_records_come_from_the_triggering_run(tmp_path, gh):
    """Without an injected `jobs`, the records are read for the triggering run id and attempt,
    not for the trusted workflow's own run."""
    pr = PR(tmp_path)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    gh.routes[f"/repos/{REPO}/actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs"] = {
        "jobs": [
            {"name": n, "steps": [{"name": k, "conclusion": v} for k, v in steps.items()]}
            for n, steps in jobs.items()
        ]
    }
    res = trusted(pr, gh, results, None)
    assert res.verdict is Verdict.PASS, res.reasons
    assert any(f"/actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs" in r for r in gh.requests)


def test_bad_inputs_are_unknown(tmp_path, gh):
    pr = PR(tmp_path)
    serve(gh, pr)
    results, jobs = run_gates(pr)
    assert trusted(pr, gh, results, jobs, head="HEAD").verdict is Verdict.UNKNOWN
    assert trusted(pr, gh, results, jobs, run_id="x").verdict is Verdict.UNKNOWN


def test_commit_status_never_carries_pr_text():
    from repomarshal.api import DoneResult

    for v in Verdict:
        r = DoneResult(v, ("<script>evil</script> from a PR",), None, None, None)
        state, desc = ci.commit_status(r)
        assert "evil" not in desc and len(desc) <= 140
        assert (state == "success") == (v is Verdict.PASS)


def test_cli_trusted_verdict_writes_the_status(tmp_path, gh, monkeypatch, capsys):
    from repomarshal import cli

    pr = PR(tmp_path)
    serve(gh, pr)
    results, jobs = run_gates(pr)
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
    assert code == 0, capsys.readouterr().out
    lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert lines["state"] == "success"
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"


# --------------------------------------------------------------------------- ci-workflow waiver


def _workflow_pr(tmp_path, monkeypatch, permit: bool = True) -> PR:
    import tests.unit.test_ci_split as split

    extra = "[completion]\nwaivers_permit_pass = true\nallow_author_waivers = true\n"
    monkeypatch.setattr(split, "POLICY", split.POLICY + (extra if permit else ""))
    pr = PR(tmp_path)
    pr.r.write(".github/CODEOWNERS", "* @MHHamdan\n")
    pr.r.git("checkout", "-q", "main")
    pr.r.write(".github/CODEOWNERS", "* @MHHamdan\n")
    pr.base = pr.r.commit("BASE: CODEOWNERS")
    pr.r.git("checkout", "-q", "pr")
    pr.r.git("merge", "-q", "--no-edit", "main")
    pr.r.write(".github/workflows/ci.yml", "name: ci\n# reviewed change\n")
    pr.pr_head = pr.checkout = pr.r.commit("PR edits ci.yml")
    return pr


def _grant(gh: FakeGitHub, *targets: str, via=None) -> None:
    from tests.unit.test_waivers import comment, label

    kw = {} if via is None else {"via": via}
    lines = "\n".join(f"repomarshal-waive {t}: I reviewed the ci.yml diff" for t in targets)
    gh.pr(
        events=[label(t, "MHHamdan", n=i + 1, **kw) for i, t in enumerate(targets)],
        comments=[comment("MHHamdan", lines)],
        repo=REPO,
        number=3,
    )


def trusted_github(pr: PR, gh: FakeGitHub, results, jobs):
    env = {
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": "t",  # noqa: S105 - fake
        "GITHUB_API_URL": gh.url,
        "PATH": pr.env()["PATH"],
        "HOME": pr.env()["HOME"],
    }
    return ci.trusted_verdict(
        pr.clone(f"trusted-{next(_clones)}"), RUN_ID, ATTEMPT, pr.pr_head, results, env,
        waivers="github", jobs=jobs,
    )  # fmt: skip


def test_reviewed_ci_yml_change_binds_with_a_human_waiver(tmp_path, gh, monkeypatch):
    pr = _workflow_pr(tmp_path, monkeypatch)
    serve(gh, pr)
    # A CI change with code is elevated risk, so integration is required and unavailable here.
    _grant(gh, ci.WORKFLOW_WAIVER, "integration")
    results, jobs = run_gates(pr)
    res = trusted_github(pr, gh, results, jobs)
    assert res.verdict is Verdict.PASS, res.reasons
    assert any(w.target == ci.WORKFLOW_WAIVER for w in res.record.waivers)
    assert any("change accepted by MHHamdan" in r for r in res.reasons)


def test_ci_yml_waiver_through_an_app_is_rejected(tmp_path, gh, monkeypatch):
    pr = _workflow_pr(tmp_path, monkeypatch)
    serve(gh, pr)
    _grant(
        gh, ci.WORKFLOW_WAIVER, "integration", via={"performed_via_github_app": {"slug": "claude"}}
    )
    results, jobs = run_gates(pr)
    res = trusted_github(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN
    assert any(ci.WORKFLOW_WAIVER in r for r in res.reasons)


def test_ci_yml_waiver_needs_the_base_policy(tmp_path, gh, monkeypatch):
    pr = _workflow_pr(tmp_path, monkeypatch, permit=False)
    serve(gh, pr)
    _grant(gh, ci.WORKFLOW_WAIVER, "integration")
    results, jobs = run_gates(pr)
    assert trusted_github(pr, gh, results, jobs).verdict is Verdict.UNKNOWN


def test_ci_yml_change_without_waiver_is_unknown_even_with_other_waivers(tmp_path, gh, monkeypatch):
    pr = _workflow_pr(tmp_path, monkeypatch)
    serve(gh, pr)
    _grant(gh, "tests", "integration")
    results, jobs = run_gates(pr)
    res = trusted_github(pr, gh, results, jobs)
    assert res.verdict is Verdict.UNKNOWN
    assert all(s == "UNKNOWN" for g, s in statuses(res).items() if not g.startswith("native:"))
