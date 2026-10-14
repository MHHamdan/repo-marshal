"""Attacks on the release workflow (.github/workflows/release.yml; docs/guides/release.md).

Steps are executed as written (their `run:` bash), with a stub `gh`, as in
tests/unit/test_release_workflow.py. The attacker can push branches and tags (a coding agent with
`contents: write`), but cannot push to the protected default branch.

What these tests rely on about git and GitHub (certainty noted):
- `git rev-parse origin/main` resolves `refs/tags/origin/main` BEFORE `refs/remotes/origin/main`
  (gitrevisions(7) order: refs/<name>, refs/tags/<name>, refs/heads/<name>, refs/remotes/<name>;
  certain, and exercised here with real git).
- actions/checkout with `fetch-depth: 0` fetches `+refs/tags/*:refs/tags/*` along with every
  branch (high confidence: checkout's "all history" refspec; not run here).
- GitHub's tag `verification.verified` is true for a signature by a key registered on ANY GitHub
  account whose verified email matches the tagger; it does not name the signer's login (certain
  for the API shape; whether a GitHub App can obtain a verified tag through the REST API is not
  verified here).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.helpers import GIT_ENV, GitRepo
from tests.unit.test_release_workflow import (
    COMMIT,
    VERIFY,
    VERSIONS,
    WORKFLOW,
    gh_stub,
    run,
    step_script,
    tag_routes,
)

failopen = pytest.mark.failopen


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, env=GIT_ENV, capture_output=True, text=True
    ).stdout.strip()


def _origin_with_side_commit(tmp: Path) -> tuple[GitRepo, str]:
    """origin: main holds an older release; branch `side` (never merged) holds the commit X to
    publish, with matching versions."""
    r = GitRepo(tmp / "origin")
    r.write("pyproject.toml", '[project]\nname = "repomarshal"\nversion = "0.0.9"\n')
    r.write("src/repomarshal/__init__.py", '__version__ = "0.0.9"\n')
    r.commit("main release")
    r.git("checkout", "-q", "-b", "side")
    r.write("pyproject.toml", '[project]\nname = "repomarshal"\nversion = "0.1.0"\n')
    r.write("src/repomarshal/__init__.py", '__version__ = "0.1.0"\n')
    r.write("src/repomarshal/backdoor.py", "print('not reviewed')\n")
    x = r.commit("unreviewed side commit")
    r.git("checkout", "-q", "main")
    return r, x


def _checkout_like_actions(origin: Path, ws: Path, sha: str) -> None:
    """What actions/checkout does with fetch-depth: 0 and ref: <sha>."""
    ws.mkdir()
    _git(ws, "init", "-q")
    _git(ws, "remote", "add", "origin", str(origin))
    _git(ws, "fetch", "-q", "--prune", "origin",
         "+refs/heads/*:refs/remotes/origin/*", "+refs/tags/*:refs/tags/*")  # fmt: skip
    _git(ws, "checkout", "-q", "--detach", sha)


@failopen
def test_tag_named_like_the_remote_default_branch_bypasses_the_branch_check(tmp_path):
    """release.yml:68 runs `git merge-base --is-ancestor "$GITHUB_SHA" "origin/$DEFAULT_BRANCH"`.
    The short name `origin/main` resolves to a TAG `refs/tags/origin/main` before the
    remote-tracking branch, and checkout fetches every tag. Pushing a tag `origin/main` that
    points at the unmerged commit X (no `v*` ruleset covers it) makes "X is on main" true."""
    origin, x = _origin_with_side_commit(tmp_path)
    origin.git("tag", "origin/main", x)  # the attacker's extra tag
    ws = tmp_path / "ws"
    _checkout_like_actions(origin.root, ws, x)
    # X really is not on main:
    really = subprocess.run(["git", "-C", str(ws), "merge-base", "--is-ancestor", x,
                             "refs/remotes/origin/main"], env=GIT_ENV, check=False)  # fmt: skip
    assert really.returncode == 1
    p = run(step_script(VERSIONS), tmp_path, gh_stub(tmp_path, {}), cwd=ws,
            TAG="v0.1.0", GITHUB_SHA=x, DEFAULT_BRANCH="main")  # fmt: skip
    assert p.returncode != 0, "unmerged commit accepted as on main:\n" + p.stdout + p.stderr


def test_unmerged_commit_without_the_decoy_tag_is_refused_defended(tmp_path):
    """Defended (control): the same unmerged commit without the decoy tag is refused."""
    origin, x = _origin_with_side_commit(tmp_path)
    ws = tmp_path / "ws"
    _checkout_like_actions(origin.root, ws, x)
    p = run(step_script(VERSIONS), tmp_path, gh_stub(tmp_path, {}), cwd=ws,
            TAG="v0.1.0", GITHUB_SHA=x, DEFAULT_BRANCH="main")  # fmt: skip
    assert p.returncode != 0


@failopen
def test_signed_tag_signer_is_not_pinned_to_the_maintainer():
    """The verify step (release.yml:32-56) accepts any tag whose `verification.verified` is
    true: a signature by ANY GitHub account's registered key (with a matching tagger email). It
    never compares the tagger, or the signing key, with the maintainer, so the signature proves
    "someone on GitHub signed", not "the maintainer released". The `pypi` environment reviewer is
    then the only control. Severity low (defence in depth); GitHub behaviour noted above."""
    script = step_script(VERIFY)
    pinned = re.search(r"tagger|signer|key_id|ALLOWED_SIGNER|allowed_signers", script)
    assert pinned, "the verify step does not pin who signed the tag"


def test_tag_pointing_at_a_tag_is_refused_defended(tmp_path):
    """Defended: an annotated tag whose object is another tag (tag of a tag) is refused, even
    when the inner tag points at GITHUB_SHA."""
    routes = tag_routes(target_type="tag", target=COMMIT)
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, routes),
            TAG="v0.1.0", GITHUB_SHA=COMMIT)  # fmt: skip
    assert p.returncode != 0 and "not the checked-out" in p.stdout


@pytest.mark.parametrize("tag", ["v0.1.0 ", "v0.1.0$(id)", "v1.2.3/../x", "v0.1.0;id"])
def test_tag_name_injection_is_refused_defended(tmp_path, tag):
    """Defended: the tag name reaches scripts only through env and must match
    ^v<maj>.<min>.<patch>$. (The check is `grep`, which is line-based, so "v0.1.0\nanything"
    would pass it; git forbids control characters in ref names, so no tag can carry one.)"""
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, tag_routes(tag)),
            TAG=tag, GITHUB_SHA=COMMIT)  # fmt: skip
    assert p.returncode != 0


def test_no_expression_is_interpolated_into_run_scripts_defended():
    """Defended: no `${{ }}` appears inside any `run:` block (values go through `env:`)."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    inside, indent, offenders = False, 0, []
    for line in lines:
        stripped = line.strip()
        width = len(line) - len(line.lstrip(" "))
        if inside and stripped and width <= indent:
            inside = False
        if stripped.startswith("run:"):
            inside, indent = True, width
            if "${{" in stripped:
                offenders.append(line)
            continue
        if inside and "${{" in line:
            offenders.append(line)
    assert not offenders, offenders


def test_pyproject_version_decoy_is_caught_by_the_build_check_defended(tmp_path):
    """Defended overall: the version guard's `sed` takes the FIRST `version = "..."` line of
    pyproject.toml in any table, so a `[tool.x] version = "0.1.0"` before `[project]` fools that
    step; but the build step then requires `dist/repomarshal-<tag version>.tar.gz` and `.whl`,
    which hatchling names from [project].version, so the release still fails."""
    r = GitRepo(tmp_path / "origin")
    r.write(
        "pyproject.toml",
        '[tool.decoy]\nversion = "0.1.0"\n\n[project]\nname = "repomarshal"\nversion = "6.6.6"\n',
    )
    r.write("src/repomarshal/__init__.py", '__version__ = "0.1.0"\n')
    sha = r.commit("release")
    ws = tmp_path / "ws"
    subprocess.run(["git", "clone", "-q", str(r.root), str(ws)], check=True, env=GIT_ENV)
    p = run(step_script(VERSIONS), tmp_path, gh_stub(tmp_path, {}), cwd=ws,
            TAG="v0.1.0", GITHUB_SHA=sha, DEFAULT_BRANCH="main")  # fmt: skip
    assert p.returncode == 0  # the guard step alone is fooled ...
    build = step_script("build the sdist and wheel")
    assert 'test -f "dist/repomarshal-$want.tar.gz"' in build  # ... the build check is not
    assert 'test -f "dist/repomarshal-$want-py3-none-any.whl"' in build
