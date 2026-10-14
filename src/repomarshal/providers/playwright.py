"""Playwright adapter: the `runtime` provider (ADR-002; roadmap SHOULD #9). EXPERIMENTAL.

Tool: the project's own `@playwright/test` (Apache-2.0), run as a subprocess; never imported or
bundled. Tested range: >=1.50,<1.65 (the JSON reporter fields used here were checked on 1.50.1,
1.56.1, 1.60.0, 1.63.0 and 1.64.0; full browser runs on 1.63.0). Any other version is UNKNOWN.

discover: for each scope (the root, or an immediate subdirectory) where, at BASE, a
playwright.config.{ts,js,mts,mjs,cts,cjs} exists and package.json lists @playwright/test (in
dependencies, devDependencies or optionalDependencies), one gate `<scope-name>:runtime-playwright`
(kind runtime, cost expensive, cwd at the scope). It is available when `npx` is on PATH or the
scope has node_modules/.bin/playwright. Its definition is the BASE config file: a change that
adds, edits, renames or deletes any playwright.config.* in the scope sets definition_changed, so
the result is UNKNOWN (runner.py).

run: `npx --no-install playwright test --reporter=json --output=<tmp>/test-results` (or the
scope's node_modules/.bin/playwright when npx is missing) in the scope, with
PLAYWRIGHT_JSON_OUTPUT_FILE pointing to a temporary file outside the repository, CI=1, and the
gate timeout (process group killed). `--output` keeps test artifacts out of the working tree, so
the run does not change the tree digest. `--no-install` means npx never downloads anything;
browsers are never installed by RepoMarshal.

Mapping (first match wins):
    not started, timed out or killed by a signal                    UNKNOWN
    JSON report missing or malformed (no integer stats)             UNKNOWN
    config.version missing or outside the tested range              UNKNOWN
    stats.unexpected > 0                                            FAIL, except when every
        unexpected test failed because the browser executable is missing: UNKNOWN
    exit 130 (interrupted)                                          UNKNOWN
    top-level errors[] non-empty (config, syntax, "No tests found") UNKNOWN
    stats.expected == 0 (nothing ran, e.g. all skipped)             UNKNOWN
    stats.flaky > 0 (unless allow_flaky)                            UNKNOWN
    exit code != 0                                                  UNKNOWN
    otherwise (exit 0)                                              PASS

Duplicate runtime gates: a scope with a Playwright provider gate may also have the package.json
script gate `<scope-name>:runtime` (test:e2e / e2e). Both are REQUIRED and both must pass, which
fails closed but runs the suite twice. The provider gate is preferred: superseded() names the
script gates that only run `playwright test` (optionally via npx, no further arguments, no
pre/post hooks); the inventory drops those when this provider gate exists in the same scope,
unless the script's definition changed in the diff (then it stays, and is UNKNOWN). A script
that does anything else (extra flags, other commands) is kept: it checks something the provider
gate does not.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from repomarshal import git
from repomarshal.model import (
    ChangeSet,
    Cost,
    Gate,
    GateKind,
    GateResult,
    ResultStatus,
    sha256_bytes,
    sha256_json,
)
from repomarshal.providers._common import (
    Version,
    first_line,
    in_range,
    parse_version,
    run_process,
    tail,
    version_text,
    which,
)

NAME = "playwright"
PACKAGE = "@playwright/test"
TESTED_LOW: Version = (1, 50, 0)
TESTED_HIGH: Version = (1, 65, 0)
TESTED_RANGE = ">=1.50,<1.65"
# Playwright's own lookup order for a config file in a directory.
CONFIG_NAMES: tuple[str, ...] = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mts",
    "playwright.config.mjs",
    "playwright.config.cts",
    "playwright.config.cjs",
)
CONFIG_PREFIX = "playwright.config."
DEPENDENCY_KEYS: tuple[str, ...] = ("dependencies", "devDependencies", "optionalDependencies")
ARGV: tuple[str, ...] = ("npx", "--no-install", "playwright", "test", "--reporter=json")
GATE_SUFFIX = "runtime-playwright"
BROWSER_MISSING = "Executable doesn't exist"
MAX_TITLES_SHOWN = 5


# --------------------------------------------------------------------------- discovery


def _scope_name(scope: str) -> str:
    """Same id prefix as inventory.py: "root" for the root, "root/" for a directory "root"."""
    if not scope:
        return "root"
    name = scope[:-1]
    return name + "/" if name.lower() == "root" else name


def _package_json(repo: Path, base: str | None, scope: str) -> dict[str, Any] | None:
    raw = git.read_at(repo, base, scope + "package.json")
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _has_playwright(pkg: dict[str, Any]) -> bool:
    return any(isinstance(pkg.get(k), dict) and PACKAGE in pkg[k] for k in DEPENDENCY_KEYS)


def _scopes(files: tuple[str, ...]) -> list[str]:
    scopes = set()
    for f in files:
        parts = f.split("/")
        if parts[-1] == "package.json" and len(parts) <= 2:
            scopes.add("" if len(parts) == 1 else parts[0] + "/")
    return sorted(scopes)


def _config_changes(scope: str, change: ChangeSet | None) -> list[str]:
    """Changed paths that are a playwright.config.* directly in the scope."""
    if change is None:
        return []
    hits = set()
    for f in change.files:
        for p in (f.path, f.old_path):
            if p and p.startswith(scope):
                rel = p[len(scope) :]
                if "/" not in rel and rel.startswith(CONFIG_PREFIX):
                    hits.add(p)
    return sorted(hits)


def _local_bin(repo: Path, scope: str) -> Path:
    return Path(repo) / scope / "node_modules" / ".bin" / "playwright"


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def discover(
    repo: Path, base: str | None, change: ChangeSet | None, path_env: str | None
) -> tuple[Gate, ...]:
    files = git.list_files_at(repo, base)
    present = frozenset(files)
    npx = which("npx", {"PATH": path_env} if path_env is not None else None) is not None
    gates = []
    for scope in _scopes(files):
        config = next((c for c in CONFIG_NAMES if scope + c in present), None)
        pkg = _package_json(repo, base, scope) if config else None
        if config is None or pkg is None or not _has_playwright(pkg):
            continue
        raw = git.read_at(repo, base, scope + config) or b""
        available = npx or _is_executable(_local_bin(repo, scope))
        changed = _config_changes(scope, change)
        source = f"{scope}{config}; provider:{NAME} ({PACKAGE} {TESTED_RANGE})"
        if changed:
            source += "; definition changed: " + ", ".join(changed[:5])
        gates.append(
            Gate(
                id=f"{_scope_name(scope)}:{GATE_SUFFIX}",
                kind=GateKind.RUNTIME,
                argv=ARGV,
                cwd=scope[:-1],
                scope=scope,
                cost=Cost.EXPENSIVE,
                source=source,
                provider=NAME,
                available=available,
                unavailable_reason=None
                if available
                else f"npx not found on PATH and no {scope}node_modules/.bin/playwright",
                definition_digest=sha256_json(
                    {"config": scope + config, "sha256": sha256_bytes(raw), "argv": list(ARGV)}
                ),
                definition_changed=bool(changed),
            )
        )
    return tuple(gates)


def superseded(
    repo: Path, base: str | None, gates: tuple[Gate, ...] | list[Gate]
) -> frozenset[str]:
    """Ids of package.json runtime script gates that a Playwright provider gate in the same scope
    replaces: the script (read at BASE) is exactly `playwright test` or `npx playwright test`,
    with no pre/post hook. Every other runtime gate is kept."""
    scopes = {g.scope for g in gates if g.provider == NAME}
    out = set()
    for g in gates:
        if g.provider != "command" or g.kind is not GateKind.RUNTIME or g.scope not in scopes:
            continue
        prefix = f"{g.scope}package.json#scripts."
        if not g.source.startswith(prefix):
            continue
        name = g.source[len(prefix) :].split(";", 1)[0]
        pkg = _package_json(repo, base, g.scope)
        scripts = pkg.get("scripts") if pkg else None
        if not isinstance(scripts, dict) or scripts.get("pre" + name) or scripts.get("post" + name):
            continue
        body = scripts.get(name)
        try:
            words = shlex.split(body) if isinstance(body, str) else []
        except ValueError:
            continue
        if words in (["playwright", "test"], ["npx", "playwright", "test"]):
            out.add(g.id)
    return frozenset(out)


# --------------------------------------------------------------------------- mapping


def _tests(suites: Any) -> list[tuple[str, dict[str, Any]]]:
    """(label, test) for every test in the suite tree."""
    out: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(suites, list):
        return out
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        for spec in suite.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            label = f"{spec.get('file', '?')} > {spec.get('title', '?')}"
            for t in spec.get("tests") or []:
                if isinstance(t, dict):
                    out.append((label, t))
        out.extend(_tests(suite.get("suites")))
    return out


def _error_text(test: dict[str, Any]) -> str:
    parts = []
    for r in test.get("results") or []:
        if not isinstance(r, dict):
            continue
        for e in [r.get("error"), *(r.get("errors") or [])]:
            if isinstance(e, dict) and isinstance(e.get("message"), str):
                parts.append(e["message"])
    return "\n".join(parts)


def _titles(labels: list[str]) -> str:
    shown = ", ".join(first_line(t, 120) for t in labels[:MAX_TITLES_SHOWN])
    if len(labels) > MAX_TITLES_SHOWN:
        shown += f" (+{len(labels) - MAX_TITLES_SHOWN} more)"
    return shown


def report_version(report: Any) -> Version | None:
    config = report.get("config") if isinstance(report, dict) else None
    return parse_version(config.get("version")) if isinstance(config, dict) else None


def classify(
    code: int | None, report_bytes: bytes | None, allow_flaky: bool = False
) -> tuple[ResultStatus, str, Version | None]:
    """(status, reason, version) for one Playwright run; see the module docstring."""
    if report_bytes is None:
        return ResultStatus.UNKNOWN, f"no JSON report written (exit code {code})", None
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ResultStatus.UNKNOWN, "JSON report is malformed; result unknown", None
    stats = report.get("stats") if isinstance(report, dict) else None
    keys = ("expected", "unexpected", "flaky", "skipped")
    if not isinstance(stats, dict) or not all(
        isinstance(stats.get(k), int) and not isinstance(stats.get(k), bool) for k in keys
    ):
        return ResultStatus.UNKNOWN, "JSON report has no valid stats; result unknown", None
    version = report_version(report)
    if version is None:
        return ResultStatus.UNKNOWN, "Playwright version (config.version) is missing", None
    if not in_range(version, TESTED_LOW, TESTED_HIGH):
        return (
            ResultStatus.UNKNOWN,
            f"Playwright {version_text(version)} is outside the tested range {TESTED_RANGE}",
            version,
        )
    expected, unexpected = stats["expected"], stats["unexpected"]
    flaky, skipped = stats["flaky"], stats["skipped"]
    counts = f"{expected} passed, {unexpected} failed, {flaky} flaky, {skipped} skipped"
    errors = report.get("errors")
    if errors is not None and not isinstance(errors, list):
        return ResultStatus.UNKNOWN, "JSON report has a malformed errors field", version

    if unexpected > 0:
        failed = [
            (label, t)
            for label, t in _tests(report.get("suites"))
            if t.get("status") == "unexpected"
        ]
        if failed and all(BROWSER_MISSING in _error_text(t) for _label, t in failed):
            return (
                ResultStatus.UNKNOWN,
                f"{counts}; the browser executable is missing (not installed for this "
                "Playwright version); result unknown",
                version,
            )
        titles = _titles([label for label, _t in failed])
        return ResultStatus.FAIL, f"{counts}; failed: {titles or 'see report'}", version
    if code == 130:
        return ResultStatus.UNKNOWN, f"interrupted (exit 130); {counts}", version
    if errors:
        first = errors[0].get("message") if isinstance(errors[0], dict) else errors[0]
        return (
            ResultStatus.UNKNOWN,
            f"{len(errors)} top-level error(s): {first_line(first) or 'unreadable'}; {counts}",
            version,
        )
    if expected == 0:
        return ResultStatus.UNKNOWN, f"no test passed ({counts}); nothing was verified", version
    if flaky > 0 and not allow_flaky:
        return ResultStatus.UNKNOWN, f"{counts}; flaky tests are not a pass", version
    if code != 0:
        return ResultStatus.UNKNOWN, f"exit code {code} without failed tests; {counts}", version
    return ResultStatus.PASS, counts, version


# --------------------------------------------------------------------------- run


def run(
    repo: Path,
    gate: Gate,
    change: ChangeSet,
    env: Mapping[str, str] | None,
    allow_flaky: bool = False,
) -> GateResult:
    root = Path(repo).resolve()
    cwd = (root / gate.cwd).resolve()
    if not cwd.is_relative_to(root) or not cwd.is_dir():
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"working directory {gate.cwd!r} is missing or outside the repository",
            argv=gate.argv,
        )
    run_env = dict(os.environ if env is None else env)
    for key in ("PLAYWRIGHT_JSON_OUTPUT_DIR", "PLAYWRIGHT_JSON_OUTPUT_NAME"):
        run_env.pop(key, None)
    run_env["CI"] = "1"
    run_env["PYTHONSAFEPATH"] = "1"

    with tempfile.TemporaryDirectory(prefix="repomarshal-playwright-") as tmp:
        tmp_path = Path(tmp).resolve()
        if tmp_path.is_relative_to(root):
            return GateResult(
                gate.id,
                gate.kind,
                ResultStatus.UNKNOWN,
                "the temporary directory is inside the repository; refusing to run",
                argv=gate.argv,
            )
        report_file = tmp_path / "report.json"
        run_env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(report_file)
        output = f"--output={tmp_path / 'test-results'}"
        npx = which("npx", run_env)
        local = cwd / "node_modules" / ".bin" / "playwright"
        if npx is not None:
            argv = [npx, *ARGV[1:], output]
        elif _is_executable(local):
            argv = [str(local), *ARGV[3:], output]
        else:
            return GateResult(
                gate.id,
                gate.kind,
                ResultStatus.UNKNOWN,
                "unavailable: npx not found on PATH and no node_modules/.bin/playwright",
                argv=gate.argv,
            )
        proc = run_process(argv, cwd, run_env, gate.timeout_s)
        report = report_file.read_bytes() if report_file.is_file() else None

    common: dict[str, Any] = dict(
        argv=gate.argv,
        exit_code=proc.code,
        duration_ms=proc.duration_ms,
        stdout_sha256=sha256_bytes(proc.out),
        stderr_sha256=sha256_bytes(proc.err),
        output_tail=tail(proc.err, proc.out),
    )
    if proc.start_error:
        return GateResult(gate.id, gate.kind, ResultStatus.UNKNOWN, proc.start_error, **common)
    if proc.timed_out:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"timed out after {gate.timeout_s}s; result unknown",
            **common,
        )
    if proc.code is not None and proc.code < 0:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"terminated by signal {-proc.code}; result unknown",
            **common,
        )
    status, reason, version = classify(proc.code, report, allow_flaky)
    tool = f"{NAME} {version_text(version)}" if version else None
    return GateResult(gate.id, gate.kind, status, reason, tool=tool, **common)
