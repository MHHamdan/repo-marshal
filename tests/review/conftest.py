"""Adversarial review suite (fail-open hunt).

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tests.acceptance.conftest import Webapp, configured_webapp, webapp  # noqa: F401


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )


def py_stub(bindir: Path, name: str, code: str) -> Path:
    """An executable stub `name` whose body is Python run by this interpreter."""
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    p.write_text(f"#!{sys.executable}\n{code}")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def real_python_stub(w: Webapp) -> None:
    """`python` on the gate PATH is this interpreter (hermetic: no real pytest is used)."""
    w.script_stub("python", f'exec "{sys.executable}" "$@"')


def fake_failing_pytest(base_dir: Path) -> dict[str, str]:
    """Env additions that make `python -m pytest` a stand-in suite that always fails."""
    fake = base_dir / "fakepy"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "pytest.py").write_text("import sys\nprint('1 failed')\nsys.exit(1)\n")
    return {"PYTHONPATH": str(fake), "PYTHONDONTWRITEBYTECODE": "1"}
