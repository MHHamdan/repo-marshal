"""Static guards on .github/workflows/ci.yml (v0.4 review): the judge-side setup must never
import modules from the PR checkout."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
SPLIT_JOBS = ("rm-plan", "rm-gate", "dogfood")


def _job(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index(f"\n  {name}:\n")
    nxt = re.search(r"\n  [a-z][a-z0-9_-]*:\n", text[start + 1 :])
    return text[start : start + 1 + nxt.start()] if nxt else text[start:]


def test_every_python_call_in_the_split_jobs_is_isolated():
    """`python -m X` / `python -` put the cwd first on sys.path; a PR file named X would run.
    Every such call in the split dogfood jobs uses -I."""
    for job in SPLIT_JOBS:
        body = "\n".join(
            line for line in _job(job).splitlines() if not line.lstrip().startswith("#")
        )
        calls = re.findall(r"(?:\bpython|/bin/python\")\s+(-\S+)", body)
        assert calls, job
        assert all(flag == "-I" for flag in calls), (job, calls)


def test_setup_steps_run_outside_the_checkout():
    for job in SPLIT_JOBS:
        body = _job(job)
        for step in ("Install the judge (RepoMarshal)", "Install the gate tools"):
            seg = body[body.index(step) :]
            seg = seg[: seg.index("        run:")]
            assert "working-directory: ${{ runner.temp }}" in seg, (job, step)


def test_gate_jobs_have_no_token_permissions():
    assert "permissions: {}" in _job("rm-gate")


def test_judge_jobs_check_out_the_run_commit():
    for job in SPLIT_JOBS:
        assert "ref: ${{ github.sha }}" in _job(job), job


def test_gate_job_step_names_match_the_binding_contract():
    from repomarshal import ci

    body = _job("rm-gate")
    for name in (ci.STEP_RUN, ci.STEP_PASSED, ci.STEP_UPLOAD):
        assert f"- name: {name}\n" in body, name
    assert "name: rm-gate-${{ matrix.index }}" in body
    assert "name: rm-gate-${{ matrix.index }}\n" in body.split("upload result")[1]
