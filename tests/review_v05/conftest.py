"""Adversarial review of v0.5: the trusted verdict (`repomarshal ci trusted-verdict`, the
default-branch `workflow_run` workflow .github/workflows/repomarshal-verdict.yml and the commit
status `repomarshal/verdict`).

Tests marked `failopen` demonstrate a confirmed fail-open path: they assert the correct
(fail-closed) behaviour and therefore FAIL on the code they were written against. Unmarked tests
are regression guards for attacks that are handled correctly ("defended" in their docstring).

No test touches the network: GitHub is the fake REST server from tests/unit/test_waivers.py, the
Actions jobs record is a dict, and workflow steps run with a stub `gh`.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "failopen: demonstrates a confirmed fail-open path (fails until fixed)"
    )
