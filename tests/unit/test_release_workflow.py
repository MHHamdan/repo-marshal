"""The release workflow (.github/workflows/release.yml): its guard steps run as written, with a
stub `gh`, and its structure is checked statically."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import GIT_ENV, GitRepo

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
REPO = "acme/repomarshal"
COMMIT = "c" * 40
TAG_SHA = "d" * 40
TAGGER = "maintainer@example.invalid"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")


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


def gh_stub(tmp: Path, routes: dict[str, object]) -> Path:
    """`gh api <path>` prints routes[path] as JSON; unknown paths fail like a 404."""
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp / "routes"
    data.mkdir(exist_ok=True)
    for i, (path, body) in enumerate(routes.items()):
        (data / f"{i}.path").write_text(path)
        (data / f"{i}.json").write_text(json.dumps(body))
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'for f in {data}/*.path; do if [ "$(cat "$f")" = "$2" ]; then '
        'cat "${f%.path}.json"; exit 0; fi; done\n'
        'echo "gh: Not Found (HTTP 404)" >&2; exit 1\n'
    )
    gh.chmod(0o755)
    return bindir


def run(script: str, tmp: Path, bindir: Path, cwd: Path | None = None, **env: str):
    (tmp / "step.sh").write_text(script)
    full = {**GIT_ENV, "PATH": f"{bindir}{os.pathsep}{GIT_ENV['PATH']}",
            "GITHUB_REPOSITORY": REPO, "ALLOWED_TAGGERS": TAGGER, **env}  # fmt: skip
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(tmp / "step.sh")],
        cwd=cwd or tmp, env=full, capture_output=True, text=True, check=False,
    )  # fmt: skip


def tag_routes(tag: str = "v0.1.0", annotated: bool = True, verified: bool = True,
               target: str = COMMIT, target_type: str = "commit",
               tagger: str = "maintainer@example.invalid") -> dict[str, object]:  # fmt: skip
    ref = {"object": {"type": "tag" if annotated else "commit", "sha": TAG_SHA}}
    obj = {
        "tagger": {"name": "Maintainer", "email": tagger},
        "object": {"type": target_type, "sha": target},
        "verification": {"verified": verified, "reason": "valid" if verified else "unsigned"},
    }
    return {f"repos/{REPO}/git/ref/tags/{tag}": ref, f"repos/{REPO}/git/tags/{TAG_SHA}": obj}


VERIFY = "verify the signed tag"


def test_signed_annotated_tag_on_the_commit_passes(tmp_path):
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, tag_routes()),
            TAG="v0.1.0", GITHUB_SHA=COMMIT)  # fmt: skip
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize(
    ("routes", "why"),
    [
        (tag_routes(annotated=False), "lightweight"),
        (tag_routes(verified=False), "not verified"),
        (tag_routes(target="e" * 40), "not the checked-out"),
        (tag_routes(target_type="tree"), "not the checked-out"),
        ({}, ""),  # the API cannot find the tag
    ],
    ids=["lightweight", "unsigned", "other-commit", "not-a-commit", "missing"],
)
def test_unverifiable_tags_are_refused(tmp_path, routes, why):
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, routes),
            TAG="v0.1.0", GITHUB_SHA=COMMIT)  # fmt: skip
    assert p.returncode != 0
    assert why in p.stdout


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "vlatest", "v0.1.0-rc1;rm"])
def test_tag_names_must_be_plain_versions(tmp_path, tag):
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, tag_routes(tag)),
            TAG=tag, GITHUB_SHA=COMMIT)  # fmt: skip
    assert p.returncode != 0


VERSIONS = "the commit is on the default branch and the versions agree"


def _checkout(tmp: Path, version: str, on_main: bool = True) -> tuple[Path, str]:
    r = GitRepo(tmp / "origin")
    r.write("pyproject.toml", f'[project]\nname = "repomarshal"\nversion = "{version}"\n')
    r.write("src/repomarshal/__init__.py", f'__version__ = "{version}"\n')
    sha = r.commit("release")
    if not on_main:
        r.git("checkout", "-q", "-b", "side")
        r.write("x", "1")
        sha = r.commit("side commit")
        r.git("checkout", "-q", "main")
    ws = tmp / "ws"
    subprocess.run(["git", "clone", "-q", str(r.root), str(ws)], check=True, env=GIT_ENV)
    subprocess.run(["git", "-C", str(ws), "checkout", "-q", sha], check=True, env=GIT_ENV)
    return ws, sha


def test_versions_and_branch_agree(tmp_path):
    ws, sha = _checkout(tmp_path, "0.1.0")
    p = run(step_script(VERSIONS), tmp_path, gh_stub(tmp_path, {}), cwd=ws,
            TAG="v0.1.0", GITHUB_SHA=sha, DEFAULT_BRANCH="main")  # fmt: skip
    assert p.returncode == 0, p.stdout + p.stderr


def test_version_mismatch_is_refused(tmp_path):
    ws, sha = _checkout(tmp_path, "0.1.0")
    p = run(step_script(VERSIONS), tmp_path, gh_stub(tmp_path, {}), cwd=ws,
            TAG="v0.2.0", GITHUB_SHA=sha, DEFAULT_BRANCH="main")  # fmt: skip
    assert p.returncode != 0 and "version mismatch" in p.stdout


def test_commit_off_the_default_branch_is_refused(tmp_path):
    ws, sha = _checkout(tmp_path, "0.1.0", on_main=False)
    p = run(step_script(VERSIONS), tmp_path, gh_stub(tmp_path, {}), cwd=ws,
            TAG="v0.1.0", GITHUB_SHA=sha, DEFAULT_BRANCH="main")  # fmt: skip
    assert p.returncode != 0


# --------------------------------------------------------------------------- structure


def _job(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index(f"\n  {name}:\n")
    nxt = re.search(r"\n  [a-z][a-z0-9_-]*:\n", text[start + 1 :])
    return text[start : start + 1 + nxt.start()] if nxt else text[start:]


def test_triggered_by_version_tags_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    on = text[text.index("\non:\n") : text.index("\npermissions:")]
    assert 'tags: ["v*"]' in on
    for other in ("pull_request", "workflow_dispatch", "branches", "workflow_run", "schedule"):
        assert other not in on, other
    assert "\npermissions: {}\n" in text


def test_only_the_publish_job_can_mint_the_pypi_token():
    build, publish = _job("build"), _job("publish")
    assert "id-token" not in build
    assert "id-token: write" in publish
    assert "name: pypi" in publish
    for bad in ("checkout", "setup-python", "python", "pip ", "password", "secrets."):
        assert bad not in publish, bad


def test_no_api_token_anywhere():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "password:" not in text.replace("# ", "")


def test_build_uses_the_tagged_commit_and_pinned_tools():
    build = _job("build")
    assert "ref: ${{ github.sha }}" in build
    assert '"build==1.2.2.post1"' in build
    assert re.search(r'requires = \["hatchling==[0-9.]+"\]', (ROOT / "pyproject.toml").read_text())


def test_versions_match_the_code():
    from repomarshal import __version__

    py = re.search(r'^version = "(.*)"$', (ROOT / "pyproject.toml").read_text(), re.M)
    assert py and py.group(1) == __version__ == "0.1.0"


@pytest.mark.parametrize(
    ("allowed", "tagger"),
    [("", "maintainer@example.invalid"), ("someone@example.invalid", "maintainer@example.invalid"),
     ("maintainer@example.invalid", "")],
    ids=["variable-unset", "other-tagger", "no-tagger"],
)  # fmt: skip
def test_tagger_must_be_allowed(tmp_path, allowed, tagger):
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, tag_routes(tagger=tagger)),
            TAG="v0.1.0", GITHUB_SHA=COMMIT, ALLOWED_TAGGERS=allowed)  # fmt: skip
    assert p.returncode != 0


def test_tagger_in_a_list_is_allowed(tmp_path):
    p = run(step_script(VERIFY), tmp_path, gh_stub(tmp_path, tag_routes()), TAG="v0.1.0",
            GITHUB_SHA=COMMIT, ALLOWED_TAGGERS=f"a@example.invalid, {TAGGER}")  # fmt: skip
    assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("extra", [None, "evil.whl"], ids=["exact", "extra-file"])
def test_publish_uploads_exactly_the_two_files(tmp_path, extra):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "repomarshal-0.1.0.tar.gz").write_text("s")
    (dist / "repomarshal-0.1.0-py3-none-any.whl").write_text("w")
    if extra:
        (dist / extra).write_text("x")
    p = run(step_script("exactly the two release files"), tmp_path, gh_stub(tmp_path, {}),
            cwd=tmp_path, GITHUB_REF_NAME="v0.1.0")  # fmt: skip
    assert (p.returncode == 0) == (extra is None), p.stdout + p.stderr
