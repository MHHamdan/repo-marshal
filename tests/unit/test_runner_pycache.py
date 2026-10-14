"""v0.6: command gates keep Python bytecode out of the working tree, and never execute a .pyc
planted in the tree (PYTHONPYCACHEPREFIX)."""

from __future__ import annotations

import importlib.util
import py_compile
import sys

from helpers import GitRepo
from repomarshal import api
from repomarshal.model import Verdict

POLICY = (
    'version = 1\n[gates.tests]\nkind = "unit"\n'
    f'argv = ["{sys.executable}", "-c", "import calc, sys; sys.exit(calc.check())"]\n'
)


def _repo(tmp_path) -> GitRepo:
    """A project with NO .gitignore, like a first-time user's repository."""
    r = GitRepo(tmp_path / "proj")
    r.write("repomarshal.toml", POLICY)
    r.write("calc.py", "def check():\n    return 0\n")
    r.commit("BASE")
    return r


def _env(r: GitRepo) -> dict:
    import os

    return {**os.environ, "PYTHONPATH": str(r.root)}


def test_gate_importing_python_does_not_change_the_tree(tmp_path):
    r = _repo(tmp_path)
    r.write("calc.py", "def check():\n    return 0  # changed\n")
    res = api.done(r.root, env=_env(r))
    assert res.verdict is Verdict.PASS, res.reasons
    assert not list(r.root.rglob("__pycache__"))


def test_planted_unchecked_pyc_is_not_executed(tmp_path):
    """An 'unchecked-hash' .pyc in __pycache__ would otherwise run instead of calc.py."""
    r = _repo(tmp_path)
    r.write("calc.py", "def check():\n    return 1  # the real code fails\n")
    evil = tmp_path / "evil.py"
    evil.write_text("def check():\n    return 0\n")
    tag = sys.implementation.cache_tag
    target = r.root / "__pycache__" / f"calc.{tag}.pyc"
    target.parent.mkdir()
    py_compile.compile(
        str(evil), cfile=str(target), dfile="calc.py",
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )  # fmt: skip
    # The in-tree location, also when this suite itself runs inside a RepoMarshal gate (whose
    # PYTHONPYCACHEPREFIX would otherwise redirect cache_from_source).
    saved, sys.pycache_prefix = sys.pycache_prefix, None
    try:
        assert importlib.util.cache_from_source(str(r.root / "calc.py")) == str(target)
    finally:
        sys.pycache_prefix = saved
    res = api.done(r.root, env=_env(r))
    assert res.verdict is Verdict.FAIL, res.reasons
