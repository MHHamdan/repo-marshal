"""The split dogfood jobs as written in ci.yml (v0.10): the real `run:` scripts of the plan,
gate and verdict steps, each in its own fresh clone as on a separate runner.

Regression for PR #8: the verdict step redirected its JSON into `repomarshal-done.json` in the
checkout. The shell creates that file before the judge starts, so the verdict job's change set
had one more untracked file than the plan job's. Both the plan hash and the working-tree digest
differed, and every isolated gate result was rejected as unbound (UNKNOWN). Binding must stay
strict; the jobs must simply see the same tree.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import GIT_ENV
from repomarshal import ci
from tests.unit.test_ci_split import PR
from tests.unit.test_waivers import FakeGitHub

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SPLIT_JOBS = ("rm-plan", "rm-gate", "dogfood")
API_ENV = {
    "GITHUB_REPOSITORY": "acme/webapp",
    "GITHUB_RUN_ID": "42",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_TOKEN": "t",  # fake
}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _job_lines(job: str) -> list[str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {job}:")
    end = next(
        (n for n in range(start + 1, len(lines)) if re.fullmatch(r"  [a-z][a-z0-9_-]*:", lines[n])),
        len(lines),
    )
    return lines[start:end]


def step_script(job: str, name: str) -> str:
    """The `run: |` body of step `name` in `job`, dedented as the runner sees it."""
    lines = _job_lines(job)
    i = next(n for n, line in enumerate(lines) if line.strip() == f"- name: {name}")
    base = _indent(lines[i]) + 2
    j = next(n for n in range(i + 1, len(lines)) if lines[n].strip() == "run: |")
    out = []
    for line in lines[j + 1 :]:
        if line.strip() and _indent(line) <= base:
            break
        out.append(line[base + 2 :])
    return "\n".join(out) + "\n"


def all_run_scripts(job: str) -> list[str]:
    names = [m.group(1) for m in (re.match(r"\s*- name: (.+)", ln) for ln in _job_lines(job)) if m]
    out = []
    for name in names:
        try:
            out.append(step_script(job, name))
        except StopIteration:  # a `uses:` step
            pass
    return out


class Runner:
    """One GitHub-hosted runner per job: its own RUNNER_TEMP and GITHUB_OUTPUT. The judge
    `$RUNNER_TEMP/rm-venv/bin/repomarshal` runs this checkout's RepoMarshal."""

    def __init__(self, tmp: Path, name: str, pr: PR):
        self.temp = tmp / f"{name}-runner-temp"
        rm = self.temp / "rm-venv" / "bin" / "repomarshal"
        rm.parent.mkdir(parents=True)
        rm.write_text(
            "#!/usr/bin/env bash\n"
            f"PYTHONPATH={json.dumps(str(ROOT / 'src'))} exec {json.dumps(sys.executable)} "
            '-m repomarshal "$@"\n'
        )
        rm.chmod(0o755)
        self.output = self.temp / "github_output"
        self.output.touch()
        self.pr = pr

    def run(self, script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
        full = {
            **self.pr.env(),
            "RUNNER_TEMP": str(self.temp),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_WORKSPACE": str(cwd),
            "BASE_SHA": self.pr.base,
            **env,
        }
        full.pop("GITHUB_ACTIONS", None)
        step = self.temp / "step.sh"
        step.write_text(script)
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(step)],
            cwd=cwd, env=full, capture_output=True, text=True, check=False,
        )  # fmt: skip

    def outputs(self) -> dict[str, str]:
        return dict(ln.split("=", 1) for ln in self.output.read_text().splitlines() if "=" in ln)


@pytest.fixture
def gh(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    with FakeGitHub() as fake:
        yield fake


def run_jobs(tmp: Path, gh: FakeGitHub, edit_expect=None):
    """rm-plan -> rm-gate-<i> -> dogfood, with each job's real step scripts."""
    pr = PR(tmp)
    plan = Runner(tmp, "plan", pr)
    res = plan.run(step_script("rm-plan", "plan"), pr.clone("plan-job"))
    assert res.returncode == 0, res.stderr
    out = plan.outputs()
    assert out["mode"] == "split" and out["ok"] == "true", out
    expect = out["expect"]
    if edit_expect:
        doc = json.loads(expect)
        edit_expect(doc)
        expect = json.dumps(doc, separators=(",", ":"))

    results = tmp / "verdict-runner-temp" / "rm-results"  # download-artifact: <name>/result.json
    jobs = []
    for entry in json.loads(out["matrix"]):
        name = f"rm-gate-{entry['index']}"
        runner = Runner(tmp, name, pr)
        checkout = pr.clone(f"{name}-job")
        env = {"RM_EXPECT": expect, "RM_GATE": entry["gate"]}
        ran = runner.run(step_script("rm-gate", "run gate"), checkout, **env)
        passed = runner.run(step_script("rm-gate", "gate passed"), runner.temp, **env)
        result = runner.temp / "rm-result" / "result.json"
        (results / name).mkdir(parents=True)
        (results / name / "result.json").write_bytes(result.read_bytes())
        steps = {
            ci.STEP_RUN: "success" if ran.returncode == 0 else "failure",
            ci.STEP_PASSED: "skipped" if ran.returncode else
            ("success" if passed.returncode == 0 else "failure"),
            ci.STEP_UPLOAD: "success",
        }  # fmt: skip
        record = [{"name": k, "conclusion": v} for k, v in steps.items()]
        jobs.append({"name": name, "steps": record})
    gh.routes["/repos/acme/webapp/actions/runs/42/attempts/1/jobs"] = {
        "total_count": len(jobs),
        "jobs": jobs,
    }

    verdict = Runner(tmp, "verdict", pr)
    checkout = pr.clone("verdict-job")
    res = verdict.run(
        step_script("dogfood", "repomarshal verdict"),
        checkout,
        RM_EXPECT=expect,
        GITHUB_API_URL=gh.url,
        **API_ENV,
    )
    return res, json.loads(out["expect"]), results, verdict, checkout


def _done_json(verdict: Runner, checkout: Path) -> dict:
    """The verdict JSON wherever the step wrote it (outside the checkout after v0.10)."""
    for place in (verdict.temp / "repomarshal-done.json", checkout / "repomarshal-done.json"):
        if place.exists() and place.stat().st_size:
            return json.loads(place.read_text())
    raise AssertionError("verdict step wrote no JSON")


def test_correctly_bound_split_run_passes(tmp_path, gh):
    res, expect, results, verdict, checkout = run_jobs(tmp_path, gh)
    done = _done_json(verdict, checkout)
    assert res.returncode == 0, (done["verdict"], done["reasons"])
    assert done["verdict"] == "PASS"
    assert not any("binding" in r or "not bound" in r for r in done["reasons"]), done["reasons"]


def test_plan_and_gate_jobs_carry_identical_binding(tmp_path, gh):
    _, expect, results, _, _ = run_jobs(tmp_path, gh)
    files = sorted(results.glob("rm-gate-*/result.json"))
    assert len(files) == len(expect["gates"]) == 2
    for f in files:
        doc = json.loads(f.read_text())
        assert doc["plan_hash"] == expect["plan_hash"], f
        assert doc["head"] == expect["head"], f
        assert doc["tree_before"] == doc["tree_after"] == expect["tree"], f


def test_verdict_step_leaves_the_checkout_untouched(tmp_path, gh):
    _, _, _, _, checkout = run_jobs(tmp_path, gh)
    status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True, env=GIT_ENV,
    ).stdout  # fmt: skip
    # .repomarshal/ holds the evidence record; the tree digest excludes it by design.
    stray = [ln for ln in status.splitlines() if not ln[3:].startswith(".repomarshal/")]
    assert stray == [], status


@pytest.mark.parametrize(
    ("field", "value"),
    [("plan_hash", "sha256:" + "0" * 64), ("tree", "0" * 40)],
)
def test_altered_binding_still_fails_closed(tmp_path, gh, field, value):
    def edit(doc):
        doc[field] = value

    res, _, _, verdict, checkout = run_jobs(tmp_path, gh, edit_expect=edit)
    done = _done_json(verdict, checkout)
    assert res.returncode == 2, (done["verdict"], done["reasons"])
    assert done["verdict"] == "UNKNOWN"
    key = "plan_hash" if field == "plan_hash" else "tree"
    assert any(f"binding: {key} differs" in r for r in done["reasons"]), done["reasons"]


# Redirect targets the split jobs may write to: never the checkout, whose tree is bound.
_SAFE_TARGET = re.compile(
    r'^"?\$(?:\{)?(?:RUNNER_TEMP|GITHUB_OUTPUT|GITHUB_STEP_SUMMARY|GITHUB_ENV)\b|^/dev/(?:null|stderr)$'
)


def test_split_jobs_never_redirect_into_the_checkout():
    for job in SPLIT_JOBS:
        for script in all_run_scripts(job):
            code = "\n".join(
                ln for ln in script.splitlines() if not ln.lstrip().startswith("#")
            ).replace("<<'PY'", "")
            for target in re.findall(r"(?<![<0-9&])>{1,2}\s*([^\s;|&)]+)", code):
                if target.startswith("&"):
                    continue
                assert _SAFE_TARGET.match(target), (job, target)
            for target in re.findall(r"\btee\s+(?:-a\s+)?([^\s;|&)]+)", code):
                assert _SAFE_TARGET.match(target), (job, target)


def test_evidence_upload_does_not_read_the_verdict_json_from_the_checkout():
    body = "\n".join(_job_lines("dogfood"))
    upload = body[body.index("- name: Upload evidence") :]
    assert "\n            repomarshal-done.json" not in upload
    assert "runner.temp }}/repomarshal-done.json" in upload


def test_ci_guide_recipes_keep_the_verdict_json_out_of_the_checkout():
    guide = (ROOT / "docs" / "guides" / "ci.md").read_text(encoding="utf-8")
    assert not re.search(r">\s*repomarshal-done\.json", guide)
