"""Attacks on the v0.6 runner change: PYTHONPYCACHEPREFIX in `runner._run` (runner.py:166).

Each command gate gets a fresh `tempfile.mkdtemp` directory as its bytecode prefix, removed after
the gate. The questions: can one gate plant bytecode that a later gate executes, and does the
prefix directory outlive the run?
"""

from __future__ import annotations

import os
import sys
import tempfile

from repomarshal import api
from repomarshal.model import Verdict
from tests.helpers import GitRepo

PLANT = r"""
import importlib.util, os, py_compile, sys, tempfile
src = os.path.join(os.getcwd(), "calc.py")
evil = os.path.join(tempfile.gettempdir(), "evil_calc.py")
with open(evil, "w") as f:
    f.write("def check():\n    return 0\n")
target = importlib.util.cache_from_source(src)  # honours sys.pycache_prefix (this gate's prefix)
os.makedirs(os.path.dirname(target), exist_ok=True)
py_compile.compile(evil, cfile=target, dfile=src,
                   invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
print("planted", target, "prefix", sys.pycache_prefix)
"""


def _policy() -> str:
    exe = _toml(sys.executable)
    check = _toml("import calc, sys; sys.exit(calc.check())")
    return (
        "version = 1\n"
        '[gates.plant]\nkind = "lint"\n'
        f'argv = [{exe}, "-c", {_toml(PLANT)}]\n'
        '[gates.tests]\nkind = "unit"\n'
        f'argv = [{exe}, "-c", {check}]\n'
    )


def _toml(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def test_bytecode_planted_by_one_gate_is_not_run_by_the_next_is_defended(tmp_path, monkeypatch):
    """Defended: gate `plant` (lint, runs first) writes an unchecked-hash .pyc for calc.py into
    its own PYTHONPYCACHEPREFIX; gate `tests` (unit) gets a different, fresh prefix, so it runs
    calc.py itself (which fails) and the verdict is FAIL. The prefixes are removed afterwards."""
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    r = GitRepo(tmp_path / "proj")
    r.write("repomarshal.toml", _policy())
    r.write("calc.py", "def check():\n    return 0\n")
    r.commit("BASE")
    r.write("calc.py", "def check():\n    return 1  # the real code fails\n")
    env = {**os.environ, "PYTHONPATH": str(r.root), "TMPDIR": str(tmp)}
    res = api.done(r.root, env=env)
    assert res.verdict is Verdict.FAIL, res.reasons
    results = {g.gate_id: g for g in res.record.results}
    assert any("planted" in line for line in results["plant"].output_tail), results["plant"]
    assert results["tests"].exit_code == 1
    assert not [p for p in tmp.iterdir() if p.name.startswith("repomarshal-pycache-")]
