"""Field-test findings (MISSION.md §43, docs/evaluation.md).

Tests marked `fieldfinding` reproduce a planner or inventory result that was wrong on a real
repository. They assert the correct behaviour and are expected to fail until it is fixed:
deselect them with `-m "not fieldfinding"`.
"""

from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "fieldfinding: reproduces a wrong plan seen on a real repository; "
        "fails until RepoMarshal is fixed (see docs/evaluation.md)",
    )
