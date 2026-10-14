"""Adversarial review of the v0.4 additions: waiver identity (performed_via_github_app,
allow_author_waivers) and split CI verification (repomarshal.ci and the ci.yml dogfood jobs).

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly ("defended" in their docstring).

No test touches the network: GitHub is the fake REST server from tests/unit/test_waivers.py, and
the Actions jobs record is a dict, as in tests/unit/test_ci_split.py.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )
