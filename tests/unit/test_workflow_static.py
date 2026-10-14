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


def test_gate_job_step_names_match_the_binding_contract():
    from repomarshal import ci

    body = _job("rm-gate")
    for name in (ci.STEP_RUN, ci.STEP_PASSED, ci.STEP_UPLOAD):
        assert f"- name: {name}\n" in body, name
    assert "name: rm-gate-${{ matrix.index }}" in body
    assert "name: rm-gate-${{ matrix.index }}\n" in body.split("upload result")[1]


def test_push_runs_only_on_main():
    """A push run on a PR branch would put a skipped `dogfood` check on the PR head commit."""
    text = WORKFLOW.read_text(encoding="utf-8")
    on = text[text.index("\non:\n") : text.index("\n  pull_request:")]
    assert "branches: [main]" in on


# ------------------------------------------------------------------ trusted workflow (v0.5)

TRUSTED = WORKFLOW.parent / "repomarshal-verdict.yml"


def _trusted() -> str:
    return TRUSTED.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_trusted_workflow_runs_only_from_workflow_run_of_ci():
    """workflow_run workflows run from the default branch: a PR cannot edit this one."""
    text = _trusted()
    on = text[text.index("\non:\n") : text.index("\npermissions:")]
    assert "workflow_run:" in on and "workflows: [ci]" in on
    for other in ("pull_request", "push", "workflow_dispatch", "pull_request_target"):
        assert other not in on, other
    assert "name: ci\n" in WORKFLOW.read_text(encoding="utf-8")  # the name it listens for


def test_trusted_workflow_permissions_are_minimal():
    text = _trusted()
    assert "\npermissions: {}\n" in text
    job = text[text.index("    permissions:\n") :]
    block = job[: job.index("    env:")]
    granted = sorted(line.split(":")[0].strip() for line in block.splitlines()[1:] if line.strip())
    assert granted == ["actions", "contents", "issues", "pull-requests", "statuses"]
    writes = [line for line in block.splitlines() if ": write" in line]
    assert len(writes) == 1 and "statuses" in writes[0]


def test_trusted_workflow_never_runs_pr_code():
    """The PR head is checked out under pr/ as data. Every run step works from RUNNER_TEMP,
    and every Python call is isolated."""
    text = _trusted()
    lines = _code_lines(text)
    body = "\n".join(lines)
    assert "path: pr\n" in body and "persist-credentials: false" in body
    calls = re.findall(r"(?:\bpython|/bin/python\")\s+(-\S+)", body)
    assert calls and all(flag == "-I" for flag in calls), calls
    steps = body.split("      - ")[1:]
    for step in steps:
        if "run: |" in step or "\n        run:" in step:
            assert "working-directory: ${{ runner.temp }}" in step, step[:80]
    # Nothing from the checkout is ever executed or sourced.
    for bad in ("pr/scripts", "./pr", "source ", ". pr/", "pip install -e", "-m pytest"):
        assert bad not in body, bad


def test_trusted_workflow_status_matches_the_judge():
    from repomarshal import ci

    text = _trusted()
    assert text.count(f"context={ci.STATUS_CONTEXT}") == 2  # pending and final
    assert "ci trusted-verdict" in text
    assert (
        ci.WORKFLOW_PATH == ".github/workflows/ci.yml"
        and Path(WORKFLOW.parent.parent.parent / ci.WORKFLOW_PATH).is_file()
    )


def test_trusted_workflow_final_status_defaults_to_error():
    """If the judge produced no state, the posted state is error, never success."""
    text = _trusted()
    post = text[text.index("- name: post the verdict status") :]
    assert "if: always()" in post.split("run:")[0]
    assert "*) STATE=error" in post


def test_gate_jobs_check_out_the_pr_head():
    for job in SPLIT_JOBS:
        assert "ref: ${{ github.event.pull_request.head.sha }}" in _job(job), job


def test_in_workflow_dogfood_is_marked_advisory():
    assert "name: dogfood (advisory)" in _job("dogfood")
