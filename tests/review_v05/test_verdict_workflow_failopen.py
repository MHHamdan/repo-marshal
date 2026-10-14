"""Attacks on the trusted workflow itself, .github/workflows/repomarshal-verdict.yml (v0.5).

Steps are executed as written (their `run:` bash, with a stub `gh` and a stub `python`), as in
tests/unit/test_trusted_workflow_steps.py. Properties that depend on GitHub's scheduler
(concurrency, workflow_run conclusions) are checked statically on the workflow text; PyYAML is
not a dev dependency, so a small line parser reads the file.

GitHub behaviour these tests rely on (see the module docstrings of each test for certainty):
- commit statuses belong to a commit SHA, not to a pull request or a base branch;
- a workflow-level `concurrency` group holds at most one pending run; a newer queued run cancels
  the pending one, whatever its jobs later decide;
- `workflow_run: workflows: [ci]` matches any workflow NAMED `ci`, on any branch and any event.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import GIT_ENV, GitRepo
from tests.unit.test_trusted_workflow_steps import gh_stub, run, step_script

ROOT = Path(__file__).resolve().parents[2]
VERDICT = ROOT / ".github" / "workflows" / "repomarshal-verdict.yml"
REPO = "acme/webapp"

INSTALL_JUDGE = "Install the judge (RepoMarshal) from the trusted workflow commit"
needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq for the gh stub")


def _text() -> str:
    return VERDICT.read_text(encoding="utf-8")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block(lines: list[str], start: int) -> list[str]:
    """The lines nested under lines[start] (deeper indentation, blank lines kept)."""
    base = _indent(lines[start])
    out = []
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= base:
            break
        out.append(line)
    return out


def _step(name: str) -> list[str]:
    lines = _text().splitlines()
    i = next(n for n, line in enumerate(lines) if line.strip() in (f"- name: {name}",))
    return [lines[i], *_block(lines, i)]


def _job_if() -> str:
    lines = _text().splitlines()
    i = next(n for n, line in enumerate(lines) if line == "  verdict:")
    body = _block(lines, i)
    for n, line in enumerate(body):
        if _indent(line) == 4 and line.strip().startswith("if:"):
            return line + "\n" + "\n".join(_block(body, n))
    return ""


def _on_block() -> str:
    lines = _text().splitlines()
    i = lines.index("on:")
    return "\n".join(_block(lines, i))


def _python_stub(bindir: Path) -> None:
    """`python -I -m venv DIR` makes DIR/bin/python, a no-op (no pip, no network)."""
    py = bindir / "python"
    py.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$2" = "-m" ] && [ "$3" = "venv" ]; then\n'
        '  mkdir -p "$4/bin"; printf "#!/usr/bin/env bash\\nexit 0\\n" > "$4/bin/python"\n'
        '  chmod +x "$4/bin/python"\n'
        "fi\n"
    )
    py.chmod(0o755)


def evil_branch_workspace(tmp: Path) -> dict[str, object]:
    """origin: main (the default branch) and `evil`, a branch the attacker pushed from main with
    its own judge; the PR branch `feature` is based on `evil`. GITHUB_WORKSPACE/pr is a full
    clone, as actions/checkout with fetch-depth 0 makes it."""
    origin = GitRepo(tmp / "origin")
    origin.write("judge.txt", "the maintainers' judge\n")
    main = origin.commit("main")
    origin.git("checkout", "-q", "-b", "evil")
    origin.write("judge.txt", "the attacker's judge: always PASS\n")
    evil = origin.commit("evil judge")
    origin.git("checkout", "-q", "-b", "feature")
    origin.write("a.txt", "change\n")
    head = origin.commit("PR")
    origin.git("checkout", "-q", "main")
    ws = tmp / "ws"
    ws.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin.root), str(ws / "pr")], check=True,
                   env=GIT_ENV)  # fmt: skip
    subprocess.run(["git", "-C", str(ws / "pr"), "checkout", "-q", "--detach", head], check=True,
                   env=GIT_ENV)  # fmt: skip
    event = tmp / "event.json"
    event.write_text(json.dumps({"repository": {"full_name": REPO, "default_branch": "main"}}))
    return {"ws": ws, "main": main, "evil": evil, "head": head, "event": event}


def default_branch_env(w: dict[str, object]) -> dict[str, str]:
    """What a workflow_run job sees: it runs on the default branch."""
    return {
        "GITHUB_WORKSPACE": str(w["ws"]),
        "GITHUB_SHA": str(w["main"]),
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_NAME": "main",
        "GITHUB_EVENT_PATH": str(w["event"]),
        "GITHUB_EVENT_NAME": "workflow_run",
    }


# --------------------------------------------------------------------------- base selection


@needs_jq
@pytest.mark.failopen
def test_find_base_refuses_a_base_branch_that_is_not_the_default_branch(tmp_path):
    """FAIL-OPEN (critical). "Find the PR base" accepts any base branch: it only checks that
    base.sha is on `origin/<base.ref>`, and base.ref is whatever branch the PR targets, including
    one the attacker just pushed. The next step installs the judge from that commit and runs its
    build (`pip install`) inside the trusted job, which holds the App secrets of the environment
    `repomarshal-verdict`. Secure: the step refuses a base that is not the default branch."""
    w = evil_branch_workspace(tmp_path)
    pulls = [
        {
            "state": "open",
            "head": {"sha": w["head"]},
            "base": {"sha": w["evil"], "ref": "evil", "repo": {"full_name": REPO}},
        }
    ]
    p = run(step_script("Find the PR base"), tmp_path, gh_stub(tmp_path, pulls),
            HEAD_SHA=str(w["head"]), **default_branch_env(w))  # fmt: skip
    out = (tmp_path / "github_output").read_text()
    assert p.returncode != 0 and f"sha={w['evil']}" not in out, (p.returncode, out)


@pytest.mark.failopen
def test_judge_is_installed_from_the_trusted_workflow_commit(tmp_path):
    """FAIL-OPEN (critical with the base choice above; medium alone). The judge's CODE comes from
    the PR's base commit, which the PR picks (any branch) and which may predate a judge fix even
    on main. The trusted workflow's own commit (GITHUB_SHA, the default branch) is the code the
    maintainers trust; only the POLICY should come from BASE. Secure: the judge installed in the
    trusted job is the default-branch one, whatever base the PR names."""
    w = evil_branch_workspace(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _python_stub(bindir)
    rt = tmp_path / "runner_temp"
    rt.mkdir()
    p = run(step_script(INSTALL_JUDGE), tmp_path,
            bindir, BASE_SHA=str(w["evil"]), RUNNER_TEMP=str(rt),
            **default_branch_env(w))  # fmt: skip
    assert p.returncode == 0, p.stderr
    installed = (rt / "rm-base" / "judge.txt").read_text()
    assert installed == "the maintainers' judge\n", installed


# --------------------------------------------------------------------------- stale statuses


@pytest.mark.failopen
def test_a_cancelled_ci_run_still_resets_the_status():
    """FAIL-OPEN (medium). The verdict job is skipped when the ci run was cancelled, on the
    assumption that a newer run for the same commit brings its own verdict. That does not hold:
    a run is also cancelled by hand (anyone with actions: write), by a push of another commit to
    the PR (ci concurrency is per PR number, not per SHA) and, as far as we know, when a job
    exceeds timeout-minutes (the PR's own test code decides that, and can read the event payload
    to do so only on, say, an `unlabeled` run). Nothing is then posted, and an earlier `success`
    on the same SHA stays: e.g. a waiver was granted (success), the maintainer removes the label,
    the re-run is cancelled, and the PR still shows the old success. Secure: a cancelled run
    posts a non-success status (error, "cancelled; re-run ci")."""
    cond = _job_if()
    assert "cancelled" not in cond, cond


@pytest.mark.failopen
def test_status_goes_pending_when_a_new_ci_run_starts():
    """FAIL-OPEN (medium). `pending` is posted only after the new ci run has COMPLETED. While it
    runs (and forever, if it is then cancelled, see above) the previous verdict on the SHA stands,
    although the inputs it was based on (waiver labels, the base branch) have changed and the new
    run was started precisely because of that. Secure: a default-branch workflow resets the status
    when a ci run is requested (workflow_run types [requested, completed])."""
    on = _on_block()
    assert re.search(r"types:\s*\[[^\]]*\brequested\b", on), on


@pytest.mark.failopen
def test_runs_that_skip_the_verdict_job_cannot_displace_a_pending_verdict():
    """FAIL-OPEN (medium). The concurrency group is workflow-level and keyed by head SHA only.
    `workflow_run: [ci]` fires for ANY workflow named `ci`: a PR branch can add one that runs on
    `push` for the same SHA. Its verdict run joins the group, cancels the pending genuine verdict
    (GitHub keeps one pending run per group), and then posts nothing, because its job is skipped
    (`event != pull_request`). If the running verdict ends in success (for instance one that read
    a waiver just before it was revoked), the genuine non-PASS is never posted. Secure: only runs
    that will actually judge enter the group (job-level concurrency after the `if`)."""
    top = [line for line in _text().splitlines() if line.startswith("concurrency:")]
    assert not top, "workflow-level concurrency lets skipped runs cancel pending verdicts"


# --------------------------------------------------------------------------- evidence


@pytest.mark.failopen
def test_evidence_artifact_has_no_pr_controlled_files():
    """FAIL-OPEN (low; integrity of the evidence, not the status). "Upload evidence" publishes
    `pr/.repomarshal/evidence/` from the PR checkout under the trusted name
    `repomarshal-verdict-evidence`. The PR can commit files there: forged PASS records sit next to
    the judge's real one, and committed symlinks there make upload-artifact read files outside the
    checkout in a job that holds the App token. Secure: the judge writes its record outside the
    checkout (RUNNER_TEMP) and only that is uploaded."""
    step = "\n".join(_step("Upload evidence"))
    assert not re.search(r"^\s*pr/", step, flags=re.MULTILINE), step


# --------------------------------------------------------------------------- defended


def test_no_expression_is_interpolated_into_a_script():
    """Defended: no `${{ }}` inside any `run:` script of the trusted workflow, so attacker
    influenced event fields (workflow_run.head_branch, display_title, head_commit.message) can
    never become shell; every value reaches the scripts through env."""
    lines = _text().splitlines()
    for n, line in enumerate(lines):
        if line.strip() == "run: |":
            script = "\n".join(_block(lines, n))
            assert "${{" not in script, script
    assert "head_branch" not in _text() and "display_title" not in _text()


def test_every_script_runs_outside_the_pr_checkout():
    """Defended: every `run:` step of the trusted job sets working-directory to the runner temp
    dir, so no tool picks up a PR file from the current directory."""
    lines = _text().splitlines()
    for n, line in enumerate(lines):
        if line.strip().startswith("- name:"):
            body = "\n".join([line, *_block(lines, n)])
            if "run: |" in body:
                assert "working-directory: ${{ runner.temp }}" in body, body


@needs_jq
def test_find_base_does_not_execute_a_branch_name(tmp_path):
    """Defended: a base ref with shell syntax is data (quoted), never executed."""
    w = evil_branch_workspace(tmp_path)
    pulls = [
        {
            "state": "open",
            "head": {"sha": w["head"]},
            "base": {"sha": w["main"], "ref": "main$(touch pwned)`touch pwned2`",
                     "repo": {"full_name": REPO}},
        }
    ]  # fmt: skip
    p = run(step_script("Find the PR base"), tmp_path, gh_stub(tmp_path, pulls),
            HEAD_SHA=str(w["head"]), **default_branch_env(w))  # fmt: skip
    assert p.returncode != 0
    assert not list(tmp_path.rglob("pwned*"))


def test_pr_gitattributes_do_not_change_the_installed_judge(tmp_path):
    """Defended: `git archive BASE` uses the attributes of the archived tree, not the PR
    checkout's working tree, so a PR `.gitattributes` with `export-ignore` / `export-subst` cannot
    strip or rewrite the judge's files."""
    origin = GitRepo(tmp_path / "origin")
    origin.write("judge.py", "VERDICT = 'real'\n")
    base = origin.commit("BASE")
    origin.git("checkout", "-q", "-b", "feature")
    origin.write(".gitattributes", "* export-ignore\njudge.py export-subst\n")
    origin.write("judge.py", "VERDICT = '$Format:%H$'\n")
    head = origin.commit("PR")
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin.root), str(ws / "pr")], check=True,
                   env=GIT_ENV)  # fmt: skip
    subprocess.run(["git", "-C", str(ws / "pr"), "checkout", "-q", "--detach", head], check=True,
                   env=GIT_ENV)  # fmt: skip
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _python_stub(bindir)
    rt = tmp_path / "runner_temp"
    rt.mkdir()
    p = run(step_script(INSTALL_JUDGE), tmp_path,
            bindir, BASE_SHA=base, GITHUB_SHA=base, RUNNER_TEMP=str(rt),
            GITHUB_WORKSPACE=str(ws))  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert (rt / "rm-base" / "judge.py").read_text() == "VERDICT = 'real'\n"
