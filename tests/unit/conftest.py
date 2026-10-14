"""Unit-test configuration: the `integration` marker for tests that run real provider tools."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: runs a real external provider tool (SkillSpector, Playwright); "
        "skipped when the tool is not installed",
    )
