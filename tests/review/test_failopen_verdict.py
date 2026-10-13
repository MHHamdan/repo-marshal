"""Fail-open paths in verdict assembly: empty change, BASE choice and gate-id collisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import GitRepo, env_with_path
from repomarshal import api
from repomarshal.model import PlanMode, Verdict


@pytest.mark.failopen
@pytest.mark.parametrize("task", ["Update installation documentation", "fix a typo in the docs"])
def test_empty_change_with_docs_task_is_not_pass(webapp, task):
    """No diff at all + task text naming docs: the FORECAST plan requires native:docs-links, which
    trivially passes on zero files, so `done` says PASS for work that does not exist."""
    plan = webapp.plan(task)
    assert plan.mode == PlanMode.FORECAST
    assert plan.change.files == ()
    res = webapp.done(task=task)
    assert res.verdict != Verdict.PASS, res.reasons
    assert res.verdict == Verdict.UNKNOWN, res.reasons


@pytest.mark.failopen
def test_moving_default_branch_ref_hides_committed_work(webapp):
    """The agent commits failing work on a feature branch, fast-forwards local `main` onto it
    (`git update-ref`), then makes a trivial docs edit. Auto-BASE becomes the agent's own commit,
    so the committed code (and any committed policy or gate-definition change) is never judged."""
    w = webapp
    w.repo.git("switch", "-q", "-c", "feature")
    w.routine_backend_change()
    w.repo.commit("agent work")
    w.script_stub("python", "echo '1 failed'\nexit 1")
    assert w.done().verdict == Verdict.FAIL  # control: the work is judged

    w.repo.git("update-ref", "refs/heads/main", "HEAD")
    w.append("docs/install.md", "\nSee the [README](../README.md).\n")
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


def test_pinned_base_defeats_ref_move(webapp):
    """Guard: with BASE pinned by the caller, moving `main` changes nothing."""
    w = webapp
    pinned = w.base
    w.repo.git("switch", "-q", "-c", "feature")
    w.routine_backend_change()
    w.repo.commit("agent work")
    w.repo.git("update-ref", "refs/heads/main", "HEAD")
    w.script_stub("python", "echo '1 failed'\nexit 1")
    res = w.done(base=pinned)
    assert res.verdict == Verdict.FAIL, res.reasons


@pytest.mark.failopen
def test_scope_named_root_collides_with_root_scope(tmp_path: Path):
    """A subdirectory called `root/` gets gate ids `root:<kind>`, the same ids as the repository
    root scope. api.done maps ids to gates with a dict, so the REQUIRED root-scope gate is
    replaced by the `root/` gate: the failing root lint/test never runs and the verdict is PASS."""
    repo = GitRepo(tmp_path / "r")
    scripts = {"scripts": {"lint": "eslint .", "test": "jest"}}
    repo.write("package.json", json.dumps(scripts))
    repo.write("root/package.json", json.dumps(scripts))
    repo.write("index.js", "module.exports = 1;\n")
    repo.write("root/a.js", "module.exports = 2;\n")
    repo.commit("BASE")
    root = repo.root.resolve()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    npm = bindir / "npm"
    # The root-scope suite fails; the `root/` suite passes.
    npm.write_text(f'#!/bin/sh\n[ "$(pwd -P)" = "{root}" ] && exit 1\nexit 0\n')
    npm.chmod(0o755)
    repo.write("index.js", "module.exports = 3;\n")

    res = api.done(repo.root, env=env_with_path(bindir))
    assert res.record is not None
    ran_in = {r.gate_id for r in res.record.results}
    assert res.verdict == Verdict.FAIL, (res.reasons, ran_in)
