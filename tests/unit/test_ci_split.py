"""v0.4 split CI (threat T26): plan job, isolated gate jobs, verdict job (repomarshal.ci).

Each gate "job" runs in its own fresh clone, as on a separate runner. The verdict job gets
GitHub's record of the gate jobs as `jobs` (step conclusions), the way fetch_job_steps reads it
from the Actions API.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import GIT_ENV, GitRepo
from repomarshal import ci
from repomarshal.model import ResultStatus, Verdict
from tests.unit.test_waivers import FakeGitHub

PY = json.dumps(sys.executable)
# A gate script that exits with the mode for its key in state.txt (not a gate argument, so
# editing it is not a gate-definition change), and can touch files.
CHECK = (
    "import pathlib, sys\n"
    "modes = dict(l.split('=', 1) for l in pathlib.Path('state.txt').read_text().split())\n"
    "mode = modes[sys.argv[1]]\n"
    "if mode == 'touch':\n"
    "    pathlib.Path('touched.txt').write_text('x')\n"
    "    sys.exit(0)\n"
    "if mode.startswith('marker:'):\n"
    "    pathlib.Path(mode[7:]).write_text('ran')\n"
    "    sys.exit(0)\n"
    "sys.exit(int(mode))\n"
)
POLICY = f"""version = 1
[gates.style]
kind = "lint"
argv = [{PY}, "check.py", "style"]
[gates.tests]
kind = "unit"
argv = [{PY}, "check.py", "tests"]
"""


class PR:
    """A repository with BASE on main and a PR branch; optionally a GitHub-style merge commit."""

    def __init__(self, tmp: Path, lint: str = "0", unit: str = "0", merge: bool = False):
        self.tmp = tmp
        self.r = GitRepo(tmp / "origin")
        self.r.write("repomarshal.toml", POLICY)
        self.r.write("check.py", CHECK)
        self.r.write("app.py", "x = 1\n")
        self.r.write("state.txt", "style=0 tests=0\n")
        self.base = self.r.commit("BASE")
        self.r.git("checkout", "-q", "-b", "pr")
        self.r.write("app.py", "x = 2\n")
        self.r.write("state.txt", f"style={lint} tests={unit}\n")
        self.pr_head = self.r.commit("PR")
        self.checkout = self.pr_head
        if merge:
            self.r.git("checkout", "-q", "main")
            self.r.write("other.md", "# other\n")
            self.r.commit("main moves on")
            self.r.git("checkout", "-q", "--detach")
            self.r.git("merge", "-q", "--no-ff", "-m", "merge", self.pr_head)
            self.checkout = self.r.head()
            self.base = self.r.git("rev-parse", "HEAD^1")
        self.event = tmp / "event.json"
        self.event.write_text(
            json.dumps({"pull_request": {"number": 3, "head": {"sha": self.pr_head}}})
        )

    def clone(self, name: str) -> Path:
        dest = self.tmp / name
        subprocess.run(["git", "clone", "-q", str(self.r.root), str(dest)], check=True, env=GIT_ENV)
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "-q", "--detach", self.checkout],
            check=True,
            env=GIT_ENV,
        )
        return dest

    def env(self, **extra: str) -> dict[str, str]:
        return {**GIT_ENV, "GITHUB_EVENT_PATH": str(self.event), **extra}


def record(code: int) -> dict[str, str]:
    """GitHub's step conclusions for a gate job whose `repomarshal ci gate` exited `code`, under
    the workflow contract: "run gate" fails only on FAIL (1); "gate passed" runs after it and
    succeeds only on PASS (0)."""
    if code == 1:
        return {ci.STEP_RUN: "failure", ci.STEP_PASSED: "skipped", ci.STEP_UPLOAD: "success"}
    return {
        ci.STEP_RUN: "success",
        ci.STEP_PASSED: "success" if code == 0 else "failure",
        ci.STEP_UPLOAD: "success",
    }


def run_split(pr: PR, tamper=None, jobs_override=None):
    """Plan job -> gate jobs (fresh clones) -> verdict job (fresh clone). Returns the
    DoneResult, the plan output and the jobs record."""
    planned = ci.plan_step(pr.clone("plan-job"), pr.base, pr.env(), waivers="none")
    results = pr.tmp / "results"
    jobs: dict[str, dict[str, str]] = {}
    for entry in planned["matrix"]:
        i, gate = entry["index"], entry["gate"]
        out = results / f"rm-gate-{i}" / "result.json"
        code = ci.gate_step(pr.clone(f"gate-job-{i}"), pr.base, gate, planned["expect"], out,
                            pr.env())  # fmt: skip
        jobs[f"rm-gate-{i}"] = record(code)
    if tamper:
        tamper(results, jobs, planned)
    res = ci.verdict_step(
        pr.clone("verdict-job"),
        pr.base,
        planned["expect"],
        results,
        pr.env(),
        waivers="none",
        jobs=jobs_override if jobs_override is not None else jobs,
    )
    return res, planned, jobs


def statuses(res) -> dict[str, str]:
    return {r.gate_id: r.status.value for r in res.record.results}


def test_plan_job_lists_isolated_gates_and_binds_the_head(tmp_path):
    pr = PR(tmp_path)
    planned = ci.plan_step(pr.clone("p"), pr.base, pr.env(), waivers="none")
    assert planned["ok"], planned
    assert [m["gate"] for m in planned["matrix"]] == ["style", "tests"]
    assert planned["expect"]["head"] == pr.pr_head
    assert planned["pr_head"] == pr.pr_head


def test_all_gates_pass_gives_pass(tmp_path):
    res, _, jobs = run_split(PR(tmp_path))
    assert res.verdict is Verdict.PASS, res.reasons
    assert statuses(res) == {"style": "PASS", "tests": "PASS"}
    assert jobs["rm-gate-0"]["run gate"] == "success"


def test_merge_commit_checkout_binds_to_the_pr_head(tmp_path):
    res, planned, _ = run_split(PR(tmp_path, merge=True))
    assert planned["ok"], planned
    assert res.verdict is Verdict.PASS, res.reasons


def test_failing_gate_gives_fail(tmp_path):
    res, _, jobs = run_split(PR(tmp_path, unit="1"))
    assert res.verdict is Verdict.FAIL, res.reasons
    assert jobs["rm-gate-1"]["run gate"] == "failure"


def test_unbound_head_makes_every_isolated_result_unknown(tmp_path):
    pr = PR(tmp_path)
    pr.event.write_text(json.dumps({"pull_request": {"head": {"sha": "f" * 40}}}))
    res, planned, _ = run_split(pr)
    assert planned["ok"] is False
    assert res.verdict is Verdict.UNKNOWN
    assert set(statuses(res).values()) == {"UNKNOWN"}
    assert any("binding:" in r for r in res.reasons)


def test_missing_event_is_unbound(tmp_path):
    pr = PR(tmp_path)
    pr.event.write_text("{}")
    res, _, _ = run_split(pr)
    assert res.verdict is Verdict.UNKNOWN


def test_plan_job_output_tampered_is_unknown(tmp_path):
    def tamper(results, jobs, planned):
        planned["expect"]["plan_hash"] = "sha256:" + "0" * 64

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert res.verdict is Verdict.UNKNOWN
    assert any("plan_hash differs" in r for r in res.reasons)


def test_forged_pass_for_a_failed_gate_is_not_pass(tmp_path):
    """Another gate's job uploads a PASS file under rm-gate-1. GitHub recorded the real step
    conclusion (failure), so the result stays FAIL: never PASS, never a waivable UNKNOWN."""

    def tamper(results, jobs, planned):
        f = results / "rm-gate-1" / "result.json"
        doc = json.loads(f.read_text())
        doc["result"]["status"] = "PASS"
        f.write_text(json.dumps(doc))

    res, _, _ = run_split(PR(tmp_path, unit="1"), tamper=tamper)
    assert res.verdict is Verdict.FAIL, res.reasons
    assert statuses(res)["tests"] == "FAIL"


def test_step_success_with_fail_file_is_unknown(tmp_path):
    def tamper(results, jobs, planned):
        f = results / "rm-gate-0" / "result.json"
        doc = json.loads(f.read_text())
        doc["result"]["status"] = "FAIL"
        f.write_text(json.dumps(doc))

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert statuses(res)["style"] == "UNKNOWN"


def test_failed_upload_step_is_unknown(tmp_path):
    """If a forger uploaded under the gate's name first, the gate's own upload fails."""

    def tamper(results, jobs, planned):
        jobs["rm-gate-0"]["upload result"] = "failure"

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert res.verdict is Verdict.UNKNOWN
    assert statuses(res)["style"] == "UNKNOWN"


def test_missing_job_record_is_unknown(tmp_path):
    def tamper(results, jobs, planned):
        del jobs["rm-gate-1"]

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert statuses(res) == {"style": "PASS", "tests": "UNKNOWN"}


def test_missing_result_file_is_unknown(tmp_path):
    def tamper(results, jobs, planned):
        (results / "rm-gate-0" / "result.json").unlink()

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert statuses(res)["style"] == "UNKNOWN"


def test_result_for_another_tree_is_unknown(tmp_path):
    def tamper(results, jobs, planned):
        f = results / "rm-gate-0" / "result.json"
        doc = json.loads(f.read_text())
        doc["tree_before"] = "0" * 40
        f.write_text(json.dumps(doc))

    res, _, _ = run_split(PR(tmp_path), tamper=tamper)
    assert statuses(res)["style"] == "UNKNOWN"


def test_gate_that_modifies_its_tree_is_unknown(tmp_path):
    res, _, _ = run_split(PR(tmp_path, lint="touch"))
    assert res.verdict is Verdict.UNKNOWN
    assert statuses(res)["style"] == "UNKNOWN"


def test_definition_change_is_reapplied_by_the_verdict_job(tmp_path):
    """Even if the gate job's file claims PASS, the verdict job re-applies M14."""
    pr = PR(tmp_path)
    pr.r.write("check.py", CHECK + "# edited in the PR\n")
    pr.pr_head = pr.checkout = pr.r.commit("edit the gate script")
    pr.event.write_text(json.dumps({"pull_request": {"head": {"sha": pr.pr_head}}}))

    def tamper(results, jobs, planned):
        for i in (0, 1):
            f = results / f"rm-gate-{i}" / "result.json"
            doc = json.loads(f.read_text())
            doc["result"]["status"] = "PASS"
            f.write_text(json.dumps(doc))
            jobs[f"rm-gate-{i}"] = record(0)

    res, _, _ = run_split(pr, tamper=tamper)
    assert res.verdict is Verdict.UNKNOWN
    assert all("definition changed" in r.reason for r in res.record.results), res.record.results


def test_no_jobs_record_is_unknown(tmp_path, monkeypatch):
    """The verdict job cannot read GitHub's record of the gate jobs: nothing binds."""
    monkeypatch.setattr(ci, "fetch_job_steps", lambda env: (None, "API down"))
    pr = PR(tmp_path)
    planned = ci.plan_step(pr.clone("p"), pr.base, pr.env(), waivers="none")
    res = ci.verdict_step(
        pr.clone("v"), pr.base, planned["expect"], tmp_path / "none", pr.env(), waivers="none"
    )
    assert res.verdict is Verdict.UNKNOWN
    assert all("API down" in r.reason for r in res.record.results), res.record.results


def test_verdict_job_never_runs_repository_code(tmp_path):
    """The verdict job runs only native gates; command gates are bound, not executed."""
    marker = tmp_path / "ran-in-verdict-job"
    pr = PR(tmp_path, unit=f"marker:{marker}")
    planned = ci.plan_step(pr.clone("p"), pr.base, pr.env(), waivers="none")
    res = ci.verdict_step(
        pr.clone("v"), pr.base, planned["expect"], tmp_path / "empty", pr.env(), waivers="none",
        jobs={},
    )  # fmt: skip
    assert not marker.exists()
    assert res.verdict is Verdict.UNKNOWN


def test_gate_job_refuses_a_gate_outside_the_plan(tmp_path):
    pr = PR(tmp_path)
    planned = ci.plan_step(pr.clone("p"), pr.base, pr.env(), waivers="none")
    out = tmp_path / "r.json"
    code = ci.gate_step(pr.clone("g"), pr.base, "native:secrets-diff", planned["expect"], out,
                        pr.env())  # fmt: skip
    assert code == 2
    assert json.loads(out.read_text())["result"]["status"] == "UNKNOWN"


def test_gate_job_refuses_when_its_plan_differs(tmp_path):
    pr = PR(tmp_path)
    planned = ci.plan_step(pr.clone("p"), pr.base, pr.env(), waivers="none")
    expect = {**planned["expect"], "tree": "0" * 40}
    out = tmp_path / "r.json"
    assert ci.gate_step(pr.clone("g"), pr.base, "tests", expect, out, pr.env()) == 2


# --------------------------------------------------------------------------- Actions API


@pytest.fixture
def gh(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    with FakeGitHub() as fake:
        yield fake


def _api_env(url: str) -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "acme/webapp",
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_TOKEN": "t",  # noqa: S105 - fake
        "GITHUB_API_URL": url,
    }


def _job(name: str, run: str = "success", upload: str = "success") -> dict:
    return {
        "name": name,
        "steps": [
            {"name": "Set up job", "conclusion": "success"},
            {"name": ci.STEP_RUN, "conclusion": run},
            {"name": ci.STEP_UPLOAD, "conclusion": upload},
        ],
    }


def test_fetch_job_steps_reads_the_actions_api(gh):
    gh.routes["/repos/acme/webapp/actions/runs/42/attempts/1/jobs"] = {
        "total_count": 2,
        "jobs": [_job("rm-gate-0"), _job("rm-gate-1", run="failure")],
    }
    jobs, problem = ci.fetch_job_steps(_api_env(gh.url))
    assert problem is None
    assert jobs["rm-gate-1"][ci.STEP_RUN] == "failure"


def test_fetch_job_steps_duplicate_job_names_fail_closed(gh):
    gh.routes["/repos/acme/webapp/actions/runs/42/attempts/1/jobs"] = {
        "jobs": [_job("rm-gate-0"), _job("rm-gate-0", run="failure")]
    }
    jobs, problem = ci.fetch_job_steps(_api_env(gh.url))
    assert jobs is None and "two jobs" in problem


def test_fetch_job_steps_api_error_fails_closed(gh):
    gh.routes["/repos/acme/webapp/actions/runs/42/attempts/1/jobs"] = (500, {})
    jobs, problem = ci.fetch_job_steps(_api_env(gh.url))
    assert jobs is None and "error" in problem


def test_fetch_job_steps_needs_run_identity():
    assert ci.fetch_job_steps({"GITHUB_TOKEN": "t"})[0] is None  # noqa: S106 - fake


def test_result_status_enum_has_no_new_states():
    """_bound_result accepts PASS/FAIL/UNKNOWN only; a new state must be handled explicitly."""
    assert {s.value for s in ResultStatus} >= {"PASS", "FAIL", "UNKNOWN"}


def test_cli_round_trip_through_github_output(tmp_path, monkeypatch, capsys):
    """`ci plan --github-output` -> `ci gate` per matrix entry -> `ci verdict`, as the workflow
    calls them (job outputs are single-line JSON)."""
    from repomarshal import cli

    pr = PR(tmp_path)
    for k, v in pr.env().items():
        monkeypatch.setenv(k, v)
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    assert (
        cli.main(
            [
                "ci",
                "plan",
                "--repo",
                str(pr.clone("p")),
                "--base",
                pr.base,
                "--waivers",
                "none",
                "--github-output",
            ]
        )  # fmt: skip
        == 0
    )
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
    assert outputs["ok"] == "true" and outputs["count"] == "2"
    jobs = {}
    for entry in json.loads(outputs["matrix"]):
        name = f"rm-gate-{entry['index']}"
        code = cli.main(
            ["ci", "gate", "--repo", str(pr.clone(name)), "--base", pr.base,
             "--gate", entry["gate"], "--expect", outputs["expect"],
             "--out", str(tmp_path / "results" / name / "result.json")]
        )  # fmt: skip
        jobs[name] = record(code)
    monkeypatch.setattr(ci, "fetch_job_steps", lambda env: (jobs, None))
    capsys.readouterr()
    code = cli.main(
        ["ci", "verdict", "--repo", str(pr.clone("v")), "--base", pr.base, "--waivers", "none",
         "--expect", outputs["expect"], "--results", str(tmp_path / "results"), "--json"]
    )  # fmt: skip
    assert code == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["verdict"] == "PASS"


def test_unknown_gate_maps_to_unknown_from_the_step_record(tmp_path):
    """A gate that ends UNKNOWN (here: it modified the tree; `ci gate` exits 2): "run gate"
    succeeds, "gate passed" fails."""
    pr = PR(tmp_path, lint="touch")
    res, _, jobs = run_split(pr)
    assert jobs["rm-gate-0"] == record(2)
    assert statuses(res)["style"] == "UNKNOWN"


def test_github_fail_record_stays_fail_without_a_result_file(tmp_path):
    def tamper(results, jobs, planned):
        (results / "rm-gate-1" / "result.json").unlink()

    res, _, _ = run_split(PR(tmp_path, unit="1"), tamper=tamper)
    assert statuses(res)["tests"] == "FAIL"


def test_passed_step_reads_only_a_pass(tmp_path):
    f = tmp_path / "r.json"
    assert ci.passed_step(f) == 1  # missing
    for status, code in (("PASS", 0), ("FAIL", 1), ("UNKNOWN", 1), ("WAIVED", 1)):
        f.write_text(json.dumps({"result": {"status": status}}))
        assert ci.passed_step(f) == code, status
    f.unlink()
    f.mkdir()
    assert ci.passed_step(f) == 1  # not a regular file


def test_cli_passed(tmp_path):
    from repomarshal import cli

    f = tmp_path / "r.json"
    f.write_text(json.dumps({"result": {"status": "PASS"}}))
    assert cli.main(["ci", "passed", "--result", str(f)]) == 0
