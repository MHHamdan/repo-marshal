"""scripts/check-commit-identity.sh: single contributor, with GitHub merge-button merges allowed."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-commit-identity.sh"
OWNER = ("Mohammed Hamdan", "mh2022ets@gmail.com")
GITHUB = ("GitHub", "noreply@github.com")


def _git(repo, *args, author=OWNER, committer=OWNER):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author[0],
        "GIT_AUTHOR_EMAIL": author[1],
        "GIT_COMMITTER_NAME": committer[0],
        "GIT_COMMITTER_EMAIL": committer[1],
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "root")
    return tmp_path


def _merge(repo, author=OWNER, committer=GITHUB, msg="Merge pull request #1 from x/y"):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "topic")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: x")
    _git(repo, "switch", "-q", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "docs: y")
    _git(repo, "merge", "-q", "--no-ff", "topic", "-m", msg, author=author, committer=committer)
    return base


def _check(repo, rng):
    return subprocess.run(["bash", str(SCRIPT), rng], cwd=repo, capture_output=True, text=True)


def test_github_merge_button_merge_passes(repo):
    base = _merge(repo)
    res = _check(repo, f"{base}..HEAD")
    assert res.returncode == 0, res.stdout


def test_github_committed_non_merge_fails(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "squash", committer=GITHUB)
    res = _check(repo, f"{base}..HEAD")
    assert res.returncode == 1 and "committer noreply@github.com" in res.stdout


def test_github_merge_by_another_author_fails(repo):
    base = _merge(repo, author=("Someone", "someone@example.com"))
    res = _check(repo, f"{base}..HEAD")
    assert res.returncode == 1 and "author someone@example.com" in res.stdout


def test_github_merge_with_ai_attribution_fails(repo):
    base = _merge(repo, msg="Merge pull request #1\n\nCo-Authored-By: Bot <noreply@anthropic.com>")
    res = _check(repo, f"{base}..HEAD")
    assert res.returncode == 1 and "forbidden attribution" in res.stdout


def test_other_merge_committer_fails(repo):
    base = _merge(repo, committer=("GitHub", "someone@example.com"))
    res = _check(repo, f"{base}..HEAD")
    assert res.returncode == 1
