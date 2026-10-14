"""Split CI verification (repomarshal.ci, v0.4): plan job -> isolated gate jobs -> verdict job.

Threat: the PR's code runs in every gate job (with the runner's artifact token), so it can upload
any file under any `rm-gate-<i>` artifact name. It cannot change GitHub's step conclusions of
another job. The jobs run in-process as in tests/unit/test_ci_split.py: a fresh clone per job and
a `jobs` dict standing in for the Actions API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tests.unit.test_ci_split as split
from helpers import GitRepo
from repomarshal import api, ci
from repomarshal.model import ResultStatus, Verdict, Waiver, WaiverSource
from tests.unit.test_ci_split import PR, run_split, statuses
from tests.unit.test_waivers import FakeGitHub

PERMIT = "\n[completion]\nwaivers_permit_pass = true\n"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")


def _index(planned: dict, gate: str) -> int:
    return next(m["index"] for m in planned["matrix"] if m["gate"] == gate)


def _edit(results: Path, i: int, **changes) -> None:
    f = results / f"rm-gate-{i}" / "result.json"
    doc = json.loads(f.read_text())
    for key, value in changes.items():
        if key in ("status", "reason"):
            doc["result"][key] = value
        else:
            doc[key] = value
    f.write_text(json.dumps(doc))


def _human_waiver(target: str) -> Waiver:
    return Waiver(
        target=target,
        reason="lint tool flaked on the runner",
        identity="maint",
        source=WaiverSource.GITHUB_LABEL,
        granted_at="2026-10-14T00:00:00Z",
        reference="acme/webapp#3 label repomarshal-waive:" + target,
    )


# --------------------------------------------------------------------------- fail-open


@pytest.mark.failopen
@pytest.mark.parametrize("how", ["forged-unknown-file", "upload-name-squatted"])
def test_forged_unknown_over_a_real_fail_cannot_be_waived_into_pass(tmp_path, monkeypatch, how):
    """FAIL-OPEN: the `style` gate really FAILS (its job's "run gate" step: failure). The PR's
    code in the `tests` gate job uploads a forged rm-gate-<style> result saying UNKNOWN with a
    plausible infrastructure reason. GitHub's step record cannot tell exit 1 (FAIL) from exit 2
    (UNKNOWN): both are "failure", so the forgery binds. A human then waives the "UNKNOWN" and the
    BASE policy lets waivers count: the verdict is PASS over a real FAIL. ("A FAIL is never
    waivable".) Variant "upload-name-squatted": the forger uploads rm-gate-<style> first, so the
    real job's "upload result" step fails and the result is an unbound UNKNOWN, equally
    waivable. Secure: an UNKNOWN whose job step failed is not distinguishable from a FAIL and
    must not become PASS through a waiver."""
    monkeypatch.setattr(split, "POLICY", split.POLICY + PERMIT)
    monkeypatch.setattr(
        api, "collect_waivers", lambda prep, mode, env, extra=None: ((_human_waiver("style"),), ())
    )
    seen = {}

    def tamper(results, jobs, planned):
        i = _index(planned, "style")
        seen["real"] = json.loads((results / f"rm-gate-{i}" / "result.json").read_text())
        if how == "forged-unknown-file":
            _edit(results, i, status="UNKNOWN", reason="ruff could not start: runner image problem")
        else:
            jobs[f"rm-gate-{i}"][ci.STEP_UPLOAD] = "failure"

    res, _, jobs = run_split(PR(tmp_path, lint="1"), tamper=tamper)
    assert seen["real"]["result"]["status"] == "FAIL"  # the gate really failed
    assert res.verdict is not Verdict.PASS, res.reasons


@pytest.mark.failopen
def test_unbound_head_never_yields_pass_even_without_isolated_gates(tmp_path):
    """FAIL-OPEN (low): the verdict job cannot bind its checkout to the PR head (the event names
    another head SHA), but the plan has only native gates, so `problems` become mere "binding:"
    reasons and the verdict is PASS for a commit that is not the PR's. Secure: a binding problem
    is never compatible with PASS."""
    r = GitRepo(tmp_path / "origin")
    r.write("README.md", "# project\n")
    base = r.commit("BASE")
    r.git("checkout", "-q", "-b", "pr")
    r.write("docs/guide.md", "# guide\n\nSee [the readme](../README.md).\n")
    r.commit("PR: docs")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 3, "head": {"sha": "f" * 40}}}))
    env = {**split.GIT_ENV, "GITHUB_EVENT_PATH": str(event)}
    planned = ci.plan_step(r.root, base, env, waivers="none")
    assert planned["ok"] is False and planned["matrix"] == []
    res = ci.verdict_step(r.root, base, planned["expect"], tmp_path / "none", env, waivers="none")
    assert any(x.startswith("binding:") for x in res.reasons), res.reasons
    assert res.verdict is not Verdict.PASS, res.reasons


@pytest.mark.failopen
def test_missing_run_attempt_is_not_read_as_attempt_one():
    """FAIL-OPEN (low): with GITHUB_RUN_ATTEMPT unset, fetch_job_steps silently reads attempt 1's
    jobs (ci.py default "1"). On a re-run (attempt 2+) that is another attempt's record. Secure:
    a missing attempt is an error, like a missing run id."""
    with FakeGitHub() as gh:
        gh.routes["/repos/acme/webapp/actions/runs/42/attempts/1/jobs"] = {
            "jobs": [split._job("rm-gate-0"), split._job("rm-gate-1")]
        }
        env = {
            "GITHUB_REPOSITORY": "acme/webapp",
            "GITHUB_RUN_ID": "42",
            "GITHUB_TOKEN": "t",  # noqa: S105 - fake
            "GITHUB_API_URL": gh.url,
        }
        jobs, problem = ci.fetch_job_steps(env)
    assert jobs is None and problem, jobs


# --------------------------------------------------------------------------- defended


def test_result_json_as_a_directory_is_unknown(tmp_path):
    """Defended: a result.json that is a directory is not read."""

    def tamper(results, jobs, planned):
        f = results / "rm-gate-0" / "result.json"
        f.unlink()
        f.mkdir()

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert statuses(res)["style"] == "UNKNOWN"
    assert res.verdict is Verdict.UNKNOWN


def test_result_json_symlinked_to_a_pass_file_is_unknown(tmp_path):
    """Defended: result.json as a symlink (even to a genuine PASS file of another gate) is not
    followed (O_NOFOLLOW)."""

    def tamper(results, jobs, planned):
        f = results / "rm-gate-1" / "result.json"
        f.unlink()
        os.symlink(results / "rm-gate-0" / "result.json", f)
        jobs["rm-gate-1"]["run gate"] = "success"

    res, _, _ = run_split(PR(tmp_path, unit="1"), tamper=tamper)
    assert statuses(res)["tests"] == "UNKNOWN"


def test_result_of_another_gate_copied_under_this_index_is_unknown(tmp_path):
    """Defended: gate 0's genuine PASS file uploaded as rm-gate-1 names the wrong gate."""

    def tamper(results, jobs, planned):
        (results / "rm-gate-1" / "result.json").write_text(
            (results / "rm-gate-0" / "result.json").read_text()
        )
        jobs["rm-gate-1"]["run gate"] = "success"

    res, _, _ = run_split(PR(tmp_path, unit="1"), tamper=tamper)
    assert statuses(res)["tests"] == "UNKNOWN"


def test_pass_files_for_native_and_unplanned_gates_are_ignored(tmp_path):
    """Defended: PASS files for a native gate or an index outside the matrix change nothing:
    native gates run in the verdict job, and only matrix indexes are read."""

    def tamper(results, jobs, planned):
        doc = json.loads((results / "rm-gate-0" / "result.json").read_text())
        for i, gate in ((7, "native:secrets-diff"), (8, "tests")):
            d = dict(doc, gate_id=gate)
            d["result"] = dict(doc["result"], gate_id=gate, status="PASS")
            (results / f"rm-gate-{i}").mkdir()
            (results / f"rm-gate-{i}" / "result.json").write_text(json.dumps(d))
            jobs[f"rm-gate-{i}"] = {ci.STEP_RUN: "success", ci.STEP_UPLOAD: "success"}

    res, _, _ = run_split(PR(tmp_path, unit="1"), tamper=tamper)
    assert res.verdict is Verdict.FAIL
    assert statuses(res)["tests"] == "FAIL"


def test_waived_status_in_a_result_file_is_unknown(tmp_path):
    """Defended: a gate job cannot claim WAIVED itself."""

    def tamper(results, jobs, planned):
        _edit(results, _index(planned, "tests"), status="WAIVED")

    res, _, _ = run_split(PR(tmp_path, unit="1"), tamper=tamper)
    # Since the fix for #2 the status comes from GitHub's record of the job: the gate failed.
    assert statuses(res)["tests"] == "FAIL"


def test_merge_commit_with_pr_head_as_first_parent_is_unbound(tmp_path):
    """Defended: only GitHub's merge shape (PR head as SECOND parent) binds."""
    pr = PR(tmp_path)
    pr.r.git("checkout", "-q", "main")
    pr.r.write("other.md", "# other\n")
    pr.r.commit("main moves on")
    pr.r.git("checkout", "-q", "--detach", pr.pr_head)
    pr.r.git("merge", "-q", "--no-ff", "-m", "reverse merge", "main")
    pr.checkout = pr.r.head()
    head, pr_head, problem = ci.head_binding(pr.clone("v"), pr.env())
    assert problem is not None and pr_head == pr.pr_head and head == pr.checkout


def test_missing_plan_output_makes_isolated_gates_unknown(tmp_path):
    """Defended: no `expect` (plan job failed or skipped) -> every isolated gate is UNKNOWN."""
    pr = PR(tmp_path)
    res = ci.verdict_step(
        pr.clone("v"), pr.base, None, tmp_path / "none", pr.env(), waivers="none", jobs={}
    )
    assert res.verdict is Verdict.UNKNOWN
    assert set(statuses(res).values()) == {"UNKNOWN"}


def test_empty_jobs_record_with_isolated_gates_is_unknown(tmp_path):
    """Defended: skipped gate jobs (no record at all) cannot yield PASS."""
    res, _, _ = run_split(PR(tmp_path), jobs_override={})
    assert res.verdict is Verdict.UNKNOWN


def test_tree_after_missing_or_different_is_unknown(tmp_path):
    """Defended: tree_after absent or different from the verdict job's tree downgrades PASS."""

    def tamper(results, jobs, planned):
        _edit(results, 0, tree_after=None)
        _edit(results, 1, tree_after="0" * 40)

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert set(statuses(res).values()) == {"UNKNOWN"}


def test_result_status_values_are_handled():
    """Guard: the binding code accepts only PASS/FAIL/UNKNOWN; WAIVED is the only other state."""
    assert {s.value for s in ResultStatus} == {"PASS", "FAIL", "UNKNOWN", "WAIVED"}
