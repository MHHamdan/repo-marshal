"""Adversarial review of the v0.3 additions (waivers, Stop-hook loop termination, provider
adapters and the superseded-gate dedupe).

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly ("defended" in their docstring).

No test touches the network: GitHub is the fake REST server from tests/unit/test_waivers.py.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )
