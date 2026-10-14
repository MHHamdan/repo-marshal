"""Adversarial review of v0.6: workspace coverage (workspace.py, inventory coverage), the
lockfile secrets exemption and the dropped integration trigger (decisions.py), the runner's
PYTHONPYCACHEPREFIX, and the release workflow (.github/workflows/release.yml).

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly ("defended" in their docstring).

No test touches the network: repositories are local, tools are stubs, GitHub is a stub `gh`.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )
