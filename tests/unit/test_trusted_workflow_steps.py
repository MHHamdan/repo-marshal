"""Run the bash of the trusted workflow's steps (repomarshal-verdict.yml) with a stub `gh`, so
the PR-base lookup and the status posting are tested as written, not as described."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import GIT_ENV, GitRepo

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "repomarshal-verdict.yml"
REPO = "acme/webapp"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq for the gh stub")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def step_script(name: str) -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    i = next(n for n, line in enumerate(lines) if line.strip() == f"- name: {name}")
    base = _indent(lines[i]) + 2
    j = next(n for n in range(i + 1, len(lines)) if lines[n].strip() == "run: |")
    out = []
    for line in lines[j + 1 :]:
        if line.strip() and _indent(line) <= base:
            break
        out.append(line[base + 2 :])
    return "\n".join(out) + "\n"


def gh_stub(tmp: Path, pulls: list[dict]) -> Path:
    """`gh api ... --jq FILTER` answers commits/<sha>/pulls from `pulls`; POSTs are logged."""
    bindir = tmp / "bin"
    bindir.mkdir()
    (tmp / "pulls.json").write_text(json.dumps(pulls))
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {tmp}/gh.log\n'
        'if [ "$2" = "-X" ]; then exit 0; fi\n'
        'filter=""; while [ $# -gt 0 ]; do [ "$1" = "--jq" ] && filter="$2"; shift; done\n'
        f'jq -r "$filter" {tmp}/pulls.json\n'
    )
    gh.chmod(0o755)
    return bindir


def run(script: str, tmp: Path, bindir: Path, **env: str) -> subprocess.CompletedProcess:
    out = tmp / "github_output"
    out.touch()
    event = tmp / "event.json"  # a workflow_run payload: the default branch is main
    if not event.exists():
        event.write_text(json.dumps({"repository": {"default_branch": "main"}}))
    full = {
        "GITHUB_EVENT_PATH": str(event),
        **GIT_ENV,
        "PATH": f"{bindir}{os.pathsep}{GIT_ENV['PATH']}",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "7",
        **env,
    }
    (tmp / "step.sh").write_text(script)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(tmp / "step.sh")],
        cwd=tmp, env=full, capture_output=True, text=True, check=False,
    )  # fmt: skip


def workspace(tmp: Path) -> tuple[Path, str, str]:
    """GITHUB_WORKSPACE with pr/ cloned like actions/checkout: origin/main has the base."""
    origin = GitRepo(tmp / "origin")
    origin.write("a.txt", "1\n")
    base = origin.commit("BASE")
    origin.git("checkout", "-q", "-b", "feature")
    origin.write("a.txt", "2\n")
    head = origin.commit("PR")
    ws = tmp / "ws"
    ws.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin.root), str(ws / "pr")], check=True,
                   env=GIT_ENV)  # fmt: skip
    return ws, base, head


def pull(head: str, base: str, repo: str = REPO, state: str = "open") -> dict:
    return {
        "state": state,
        "head": {"sha": head},
        "base": {"sha": base, "ref": "main", "repo": {"full_name": repo}},
    }


def test_find_base_returns_the_single_open_prs_base(tmp_path):
    ws, base, head = workspace(tmp_path)
    p = run(step_script("Find the PR base"), tmp_path, gh_stub(tmp_path, [pull(head, base)]),
            HEAD_SHA=head, GITHUB_WORKSPACE=str(ws))  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert f"sha={base}" in (tmp_path / "github_output").read_text()


@pytest.mark.parametrize("case", ["none", "two", "closed", "fork-base"])
def test_find_base_fails_without_exactly_one_open_pr(tmp_path, case):
    ws, base, head = workspace(tmp_path)
    pulls = {
        "none": [],
        "two": [pull(head, base), pull(head, base)],
        "closed": [pull(head, base, state="closed")],
        "fork-base": [pull(head, base, repo="evil/fork")],
    }[case]
    p = run(step_script("Find the PR base"), tmp_path, gh_stub(tmp_path, pulls),
            HEAD_SHA=head, GITHUB_WORKSPACE=str(ws))  # fmt: skip
    assert p.returncode != 0
    assert "sha=" not in (tmp_path / "github_output").read_text()


def test_find_base_refuses_a_base_that_is_not_on_the_base_branch(tmp_path):
    """The judge is installed from the base commit; it must come from the base branch."""
    ws, base, head = workspace(tmp_path)
    p = run(step_script("Find the PR base"), tmp_path, gh_stub(tmp_path, [pull(head, head)]),
            HEAD_SHA=head, GITHUB_WORKSPACE=str(ws))  # fmt: skip
    assert p.returncode != 0


@pytest.mark.parametrize(
    ("state", "posted"),
    [("success", "success"), ("failure", "failure"), ("error", "error"), ("", "error"),
     ("success; rm -rf /", "error"), ("pending", "error")],
)  # fmt: skip
def test_post_status_only_passes_through_known_states(tmp_path, state, posted):
    bindir = gh_stub(tmp_path, [])
    p = run(step_script("post the verdict status"), tmp_path, bindir, HEAD_SHA="a" * 40,
            STATE=state, DESCRIPTION="d")  # fmt: skip
    assert p.returncode == 0, p.stderr
    log = (tmp_path / "gh.log").read_text()
    assert f"state={posted} " in log and "context=repomarshal/verdict" in log
    assert f"repos/{REPO}/statuses/{'a' * 40}" in log


def test_find_base_refuses_a_pr_into_another_branch(tmp_path):
    ws, base, head = workspace(tmp_path)
    other = pull(head, base)
    other["base"]["ref"] = "feature"
    p = run(step_script("Find the PR base"), tmp_path, gh_stub(tmp_path, [other]),
            HEAD_SHA=head, GITHUB_WORKSPACE=str(ws))  # fmt: skip
    assert p.returncode != 0 and "not the default branch" in p.stdout


@pytest.mark.parametrize("state", ["skip"])
def test_post_status_skip_posts_nothing(tmp_path, state):
    bindir = gh_stub(tmp_path, [])
    p = run(step_script("post the verdict status"), tmp_path, bindir, HEAD_SHA="a" * 40,
            STATE=state, DESCRIPTION="d")  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert not (tmp_path / "gh.log").exists()
