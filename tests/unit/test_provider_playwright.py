"""Playwright adapter (runtime provider; ADR-002, roadmap SHOULD #9).

Unit tests replay real `playwright test --reporter=json` reports recorded with @playwright/test
1.63.0 (tests/fixtures/providers/playwright/) through a stub `npx`. The integration tests at the
end run real Playwright; they need `npx` on PATH and REPOMARSHAL_PLAYWRIGHT_NODE_MODULES pointing
at a node_modules directory with @playwright/test installed (and, when the Playwright version's
own browser build is not installed, PW_CHROMIUM_EXECUTABLE pointing at a Chromium binary).
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import providers, runner
from repomarshal.model import (
    ChangedFile,
    ChangeSet,
    Cost,
    FileStatus,
    Gate,
    GateKind,
    ResultStatus,
)
from repomarshal.providers import playwright as pw

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
REPORTS = FIXTURES / "playwright"
STUB = FIXTURES / "replay_stub.py"
EMPTY = ChangeSet(None, None, "0" * 40, (), True)


def stub(bin_dir: Path, name: str = "npx") -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / name
    p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" "$@"\n')
    p.chmod(0o755)
    return p


def load(name: str) -> dict:
    return json.loads((REPORTS / f"{name}.json").read_text())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "web").mkdir(parents=True)
    return root


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    stub(bin_dir)
    e = dict(os.environ)
    e["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    e["REPLAY_LOG"] = str(tmp_path / "calls.jsonl")
    e["PLAYWRIGHT_JSON_OUTPUT_DIR"] = "/should/be/removed"
    return e


def gate(scope: str = "web/") -> Gate:
    return Gate(
        id=f"{scope[:-1] or 'root'}:runtime-playwright",
        kind=GateKind.RUNTIME,
        argv=pw.ARGV,
        cwd=scope[:-1],
        scope=scope,
        cost=Cost.EXPENSIVE,
        source=f"{scope}playwright.config.ts",
        provider="playwright",
    )


def calls(env) -> list[dict]:
    path = Path(env["REPLAY_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def replay(project, env, tmp_path, report, code=0, allow_flaky=False, g=None):
    e = dict(env, REPLAY_EXIT=str(code))
    if report is not None:
        src = tmp_path / "report.json"
        if isinstance(report, dict):
            src.write_text(json.dumps(report))
        elif isinstance(report, bytes):
            src.write_bytes(report)
        else:
            src.write_bytes(Path(report).read_bytes())
        e["REPLAY_REPORT"] = str(src)
    return pw.run(project, g or gate(), EMPTY, e, allow_flaky=allow_flaky)


# --------------------------------------------------------------------------- mapping table


@pytest.mark.parametrize(
    "fixture, code, expected, needle",
    [
        ("pass", 0, ResultStatus.PASS, "1 passed, 0 failed, 0 flaky, 0 skipped"),
        ("fail", 1, ResultStatus.FAIL, "failed: fail.spec.js > heading is wrong"),
        ("flaky", 0, ResultStatus.UNKNOWN, "flaky tests are not a pass"),
        ("skipped_only", 0, ResultStatus.UNKNOWN, "nothing was verified"),
        ("no_tests", 1, ResultStatus.UNKNOWN, "top-level error(s): Error: No tests found"),
        ("errors", 1, ResultStatus.UNKNOWN, "2 top-level error(s): SyntaxError"),
        ("interrupted", 130, ResultStatus.UNKNOWN, "interrupted (exit 130)"),
        ("no_browser", 1, ResultStatus.UNKNOWN, "browser executable is missing"),
    ],
)
def test_recorded_reports_map_to_results(project, env, tmp_path, fixture, code, expected, needle):
    r = replay(project, env, tmp_path, REPORTS / f"{fixture}.json", code)
    assert r.status is expected, r.reason
    assert needle in r.reason
    assert r.tool == "playwright 1.63.0"
    assert r.exit_code == code and r.kind is GateKind.RUNTIME


def test_flaky_passes_only_when_policy_allows(project, env, tmp_path):
    r = replay(project, env, tmp_path, REPORTS / "flaky.json", 0, allow_flaky=True)
    assert r.status is ResultStatus.PASS


def test_unexpected_wins_over_interrupt_and_errors(project, env, tmp_path):
    report = load("fail")
    report["errors"] = [{"message": "Error: boom"}]
    assert replay(project, env, tmp_path, report, 130).status is ResultStatus.FAIL


def test_failure_not_caused_by_missing_browser_is_fail(project, env, tmp_path):
    report = load("no_browser")
    report["suites"][0]["specs"][0]["tests"][0]["results"][0]["error"]["message"] = "Timeout"
    for r in report["suites"][0]["specs"][0]["tests"][0]["results"]:
        r["errors"] = [{"message": "Timeout"}]
    assert replay(project, env, tmp_path, report, 1).status is ResultStatus.FAIL


def test_nonzero_exit_without_failures_is_unknown(project, env, tmp_path):
    r = replay(project, env, tmp_path, REPORTS / "pass.json", 1)
    assert r.status is ResultStatus.UNKNOWN and "exit code 1" in r.reason


@pytest.mark.parametrize("version", ["1.65.0-next", "1.49.1", "2.0.0", None])
def test_version_outside_tested_range_is_unknown(project, env, tmp_path, version):
    report = load("fail")
    if version is None:
        del report["config"]["version"]
    else:
        report["config"]["version"] = version
    r = replay(project, env, tmp_path, report, 1)
    assert r.status is ResultStatus.UNKNOWN
    if version:
        assert "outside the tested range >=1.50,<1.65" in r.reason
    else:
        assert "config.version" in r.reason and r.tool is None


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b'{"stats": ',
        b"[]",
        json.dumps({"config": {"version": "1.63.0"}}).encode(),
        json.dumps({"config": {"version": "1.63.0"}, "stats": {"expected": "1"}}).encode(),
        json.dumps(
            {
                "config": {"version": "1.63.0"},
                "stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0},
                "errors": "oops",
            }
        ).encode(),
    ],
)
def test_malformed_json_is_unknown(project, env, tmp_path, payload):
    assert replay(project, env, tmp_path, payload).status is ResultStatus.UNKNOWN


def test_missing_report_is_unknown(project, env, tmp_path):
    r = replay(project, env, tmp_path, None, 0)
    assert r.status is ResultStatus.UNKNOWN and "no JSON report" in r.reason


def test_missing_tool_is_unknown(project, env, tmp_path):
    e = dict(env, PATH=str(tmp_path / "empty"))
    r = pw.run(project, gate(), EMPTY, e)
    assert r.status is ResultStatus.UNKNOWN and "npx not found" in r.reason


def test_timeout_is_unknown(project, env, tmp_path):
    e = dict(env, REPLAY_SLEEP="5")
    r = replay(
        project, e, tmp_path, REPORTS / "pass.json", 0, g=dataclasses.replace(gate(), timeout_s=1)
    )
    assert r.status is ResultStatus.UNKNOWN and "timed out" in r.reason


def test_missing_scope_dir_is_unknown(project, env, tmp_path):
    r = pw.run(project, gate("gone/"), EMPTY, env)
    assert r.status is ResultStatus.UNKNOWN and "missing or outside" in r.reason


# --------------------------------------------------------------------------- invocation


def test_invocation_env_and_cwd(project, env, tmp_path):
    replay(project, env, tmp_path, REPORTS / "pass.json")
    (call,) = calls(env)
    assert call["argv"][:4] == ["--no-install", "playwright", "test", "--reporter=json"]
    (output,) = call["argv"][4:]
    assert output.startswith("--output=") and not Path(output[9:]).is_relative_to(project)
    assert Path(call["cwd"]) == (project / "web").resolve()
    assert call["env"]["CI"] == "1" and call["env"]["PLAYWRIGHT_JSON_OUTPUT_DIR"] is None
    report = Path(call["env"]["PLAYWRIGHT_JSON_OUTPUT_FILE"])
    assert not report.is_relative_to(project.resolve()) and not report.exists()  # cleaned up


def test_local_bin_is_used_without_npx(project, tmp_path):
    local = stub(project / "web" / "node_modules" / ".bin", "playwright")
    src = tmp_path / "report.json"
    src.write_bytes((REPORTS / "pass.json").read_bytes())
    log = tmp_path / "log.jsonl"
    e = dict(os.environ, PATH=str(tmp_path / "empty"), REPLAY_REPORT=str(src), REPLAY_LOG=str(log))
    r = pw.run(project, gate(), EMPTY, e)
    assert r.status is ResultStatus.PASS
    assert json.loads(log.read_text())["argv"][:2] == ["test", "--reporter=json"]
    assert local.exists()


def test_output_tail_is_redacted(project, env, tmp_path):
    e = dict(env, REPLAY_STDERR="token=supersecretvalue123")
    r = replay(project, e, tmp_path, REPORTS / "pass.json")
    assert any("[REDACTED]" in line for line in r.output_tail)
    assert not any("supersecretvalue123" in line for line in r.output_tail)


def test_failing_titles_are_redacted(project, env, tmp_path):
    report = load("fail")
    report["suites"][0]["specs"][0]["title"] = "leaks Authorization: Bearer abcdefghijklmnop"
    r = replay(project, env, tmp_path, report, 1)
    assert r.status is ResultStatus.FAIL and "abcdefghijklmnop" not in r.reason


def test_runner_dispatch_and_definition_change(project, env, tmp_path):
    src = tmp_path / "report.json"
    src.write_bytes((REPORTS / "pass.json").read_bytes())
    e = dict(env, REPLAY_REPORT=str(src))
    assert runner.run_gate(project, gate(), EMPTY, e).status is ResultStatus.PASS
    changed = dataclasses.replace(gate(), definition_changed=True)
    r = runner.run_gate(project, changed, EMPTY, e)
    assert r.status is ResultStatus.UNKNOWN and "definition changed" in r.reason


# --------------------------------------------------------------------------- discovery

CONFIG = "export default {};\n"
PKG = {"name": "web", "devDependencies": {"@playwright/test": "1.63.0"}}


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[GitRepo, str]:
    r = GitRepo(tmp_path / "r")
    for path, content in files.items():
        r.write(path, content)
    return r, r.commit()


def test_providers_lists_implemented_adapters():
    assert "playwright" in providers.PROVIDERS


def test_discover_scopes(tmp_path):
    r, base = _repo(
        tmp_path,
        {
            "package.json": json.dumps(PKG),
            "playwright.config.mjs": CONFIG,
            "web/package.json": json.dumps(PKG),
            "web/playwright.config.ts": CONFIG,
            "root/package.json": json.dumps({"dependencies": {"@playwright/test": "^1.63"}}),
            "root/playwright.config.cjs": CONFIG,
            "nodep/package.json": json.dumps({"devDependencies": {"jest": "1"}}),
            "nodep/playwright.config.ts": CONFIG,
            "noconfig/package.json": json.dumps(PKG),
            "deep/a/package.json": json.dumps(PKG),
            "deep/a/playwright.config.ts": CONFIG,
            "bad/package.json": "{",
            "bad/playwright.config.ts": CONFIG,
        },
    )
    bin_dir = tmp_path / "bin"
    stub(bin_dir)
    gates = {g.id: g for g in pw.discover(r.root, base, None, str(bin_dir))}
    assert set(gates) == {
        "root:runtime-playwright",
        "web:runtime-playwright",
        "root/:runtime-playwright",
    }
    g = gates["web:runtime-playwright"]
    assert (g.kind, g.provider, g.scope, g.cwd, g.cost.value) == (
        GateKind.RUNTIME,
        "playwright",
        "web/",
        "web",
        "expensive",
    )
    assert (
        g.available and not g.definition_changed and g.source.startswith("web/playwright.config.ts")
    )
    assert gates["root:runtime-playwright"].scope == ""


def test_discover_reads_base_not_the_working_tree(tmp_path):
    r, base = _repo(tmp_path, {"web/package.json": json.dumps(PKG)})
    r.write("web/playwright.config.ts", CONFIG)  # added by the change: not a gate at BASE
    assert pw.discover(r.root, base, None, None) == ()


def test_discover_availability(tmp_path):
    r, base = _repo(
        tmp_path, {"web/package.json": json.dumps(PKG), "web/playwright.config.ts": CONFIG}
    )
    (g,) = pw.discover(r.root, base, None, str(tmp_path / "empty"))
    assert not g.available and "npx not found on PATH" in g.unavailable_reason
    stub(r.root / "web/node_modules/.bin", "playwright")
    (g,) = pw.discover(r.root, base, None, str(tmp_path / "empty"))
    assert g.available


@pytest.mark.parametrize(
    "changed",
    [
        ChangedFile("web/playwright.config.ts", FileStatus.MODIFIED),
        ChangedFile("web/playwright.config.js", FileStatus.ADDED),  # .ts still wins, but flag it
        ChangedFile("web/pw.config.ts", FileStatus.RENAMED, old_path="web/playwright.config.ts"),
    ],
)
def test_config_change_marks_definition_changed(tmp_path, changed):
    r, base = _repo(
        tmp_path, {"web/package.json": json.dumps(PKG), "web/playwright.config.ts": CONFIG}
    )
    c = ChangeSet(base, base, "0" * 40, (changed,), True)
    (g,) = pw.discover(r.root, base, c, None)
    assert g.definition_changed and "definition changed" in g.source


def test_spec_change_is_not_a_definition_change(tmp_path):
    r, base = _repo(
        tmp_path, {"web/package.json": json.dumps(PKG), "web/playwright.config.ts": CONFIG}
    )
    c = ChangeSet(
        base, base, "0" * 40, (ChangedFile("web/tests/a.spec.ts", FileStatus.MODIFIED),), True
    )
    (g,) = pw.discover(r.root, base, c, None)
    assert not g.definition_changed


def test_superseded_script_gates(tmp_path):
    scripts = {
        "test:e2e": "playwright test",
        "e2e": "npx playwright test",
    }
    r, base = _repo(
        tmp_path,
        {
            "web/package.json": json.dumps({**PKG, "scripts": {"test:e2e": "playwright test"}}),
            "web/playwright.config.ts": CONFIG,
            "app/package.json": json.dumps({**PKG, "scripts": {"e2e": scripts["e2e"]}}),
            "app/playwright.config.ts": CONFIG,
            "flags/package.json": json.dumps(
                {**PKG, "scripts": {"test:e2e": "playwright test --project=chromium"}}
            ),
            "flags/playwright.config.ts": CONFIG,
            "hook/package.json": json.dumps(
                {**PKG, "scripts": {"test:e2e": "playwright test", "pretest:e2e": "node seed.js"}}
            ),
            "hook/playwright.config.ts": CONFIG,
            "cy/package.json": json.dumps({"scripts": {"test:e2e": "playwright test"}}),
        },
    )
    from repomarshal import inventory
    from repomarshal.model import Policy

    inv = inventory.build_inventory(r.root, base, Policy(), path_env=None)
    ids = {g.id for g in inv.gates}
    # the inventory drops the plain `playwright test` script gates the provider replaces
    assert not ids & {"web:runtime", "app:runtime"}
    assert {"web:runtime-playwright", "app:runtime-playwright"} <= ids
    assert providers.superseded(r.root, base, inv.gates) == frozenset()
    assert {"flags:runtime", "flags:runtime-playwright", "hook:runtime", "cy:runtime"} <= ids
    assert "cy:runtime-playwright" not in ids  # no config, no @playwright/test: no provider gate


# --------------------------------------------------------------------------- real tool

NODE_MODULES = os.environ.get("REPOMARSHAL_PLAYWRIGHT_NODE_MODULES")
needs_playwright = pytest.mark.skipif(
    shutil.which("npx") is None
    or not NODE_MODULES
    or not (Path(NODE_MODULES) / "@playwright" / "test").is_dir(),
    reason="Playwright is not installed (needs npx and REPOMARSHAL_PLAYWRIGHT_NODE_MODULES)",
)
REAL_CONFIG = """const { defineConfig } = require('@playwright/test');
const executablePath = process.env.PW_CHROMIUM_EXECUTABLE || undefined;
module.exports = defineConfig({
  testDir: './tests',
  retries: 0,
  workers: 1,
  use: { headless: true, launchOptions: executablePath ? { executablePath } : {} },
});
"""
PASS_SPEC = """const { test, expect } = require('@playwright/test');
test('greeting renders', async ({ page }) => {
  await page.setContent('<h1 id="t">Hello</h1>');
  await expect(page.locator('#t')).toHaveText('Hello');
});
"""
FAIL_SPEC = """const { test, expect } = require('@playwright/test');
test('heading is wrong', async ({ page }) => {
  await page.setContent('<h1 id="t">Hello</h1>');
  await expect(page.locator('#t')).toHaveText('Goodbye', { timeout: 500 });
});
"""


def _real_project(tmp_path: Path) -> tuple[GitRepo, str]:
    version = json.loads((Path(NODE_MODULES) / "@playwright/test/package.json").read_text())[
        "version"
    ]
    r = GitRepo(tmp_path / "r")
    r.write(".gitignore", "node_modules\n")
    r.write(
        "web/package.json",
        json.dumps({"private": True, "devDependencies": {"@playwright/test": version}}),
    )
    r.write("web/playwright.config.js", REAL_CONFIG)
    r.write("web/tests/pass.spec.js", PASS_SPEC)
    base = r.commit()
    (r.root / "web/node_modules").symlink_to(Path(NODE_MODULES).resolve())
    return r, base


@pytest.mark.integration
@needs_playwright
def test_real_playwright_pass_and_fail(tmp_path):
    r, base = _real_project(tmp_path)
    (g,) = pw.discover(r.root, base, None, os.environ.get("PATH"))
    assert g.id == "web:runtime-playwright" and g.available

    ok = pw.run(r.root, g, EMPTY, None)
    assert ok.status is ResultStatus.PASS, (ok.reason, ok.output_tail)
    assert ok.tool and ok.tool.startswith("playwright 1.")
    assert not (r.root / "web/test-results").exists()  # artifacts went to a temp dir

    r.write("web/tests/fail.spec.js", FAIL_SPEC)
    bad = pw.run(r.root, g, EMPTY, None)
    assert bad.status is ResultStatus.FAIL, (bad.reason, bad.output_tail)
    assert "heading is wrong" in bad.reason and bad.exit_code == 1


@pytest.mark.integration
@needs_playwright
def test_real_playwright_through_done(tmp_path):
    from repomarshal import api

    r, _ = _real_project(tmp_path)
    r.write("web/src/Button.tsx", "export const B = () => null;\n")  # frontend.ui -> runtime
    r.write("web/tests/fail.spec.js", FAIL_SPEC)
    result = api.done(r.root, env=dict(os.environ))
    (res,) = [x for x in result.record.results if x.gate_id == "web:runtime-playwright"]
    assert res.status is ResultStatus.FAIL and res.tool.startswith("playwright ")
    assert result.verdict.value == "FAIL"


def test_superseded_gate_kept_when_its_definition_changed(tmp_path):
    """Dropping a duplicate must not hide an edit to it: `"test:e2e": "exit 0"` stays a gate
    with definition_changed, so its result is UNKNOWN."""
    r, base = _repo(
        tmp_path,
        {
            "web/package.json": json.dumps({**PKG, "scripts": {"test:e2e": "playwright test"}}),
            "web/playwright.config.ts": CONFIG,
        },
    )
    (r.root / "web/package.json").write_text(json.dumps({**PKG, "scripts": {"test:e2e": "exit 0"}}))
    from repomarshal import inventory
    from repomarshal.model import Policy

    inv = inventory.build_inventory(r.root, base, Policy(), path_env=None)
    by_id = {g.id: g for g in inv.gates}
    assert by_id["web:runtime"].definition_changed
    assert "web:runtime-playwright" in by_id
