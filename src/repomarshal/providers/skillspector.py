"""SkillSpector adapter: the `skill_audit` provider (ADR-002; roadmap SHOULD #8). EXPERIMENTAL.

Tool: NVIDIA SkillSpector (Apache-2.0), a user-installed CLI; never imported, vendored or added
as a dependency. Tested range: >=2.12,<2.13 (developed against tag v2.12.0); any other version is
UNKNOWN.

discover: when the change adds, modifies or renames a file in a skill directory
(signals.skill_dir: `.agents/skills/<n>/`, `.claude/skills/<n>/`, `skills/<n>/`, or a directory
with a SKILL.md), one gate `skills:audit` (kind skill_audit, scope "", cost expensive). With
DISCOVER_AT_BASE the gate is also contributed when skills exist only at BASE. Without
`skillspector` on PATH the gate is available=False, "skillspector not found on PATH", so a
required skill audit is UNAVAILABLE (UNKNOWN), never silently skipped.

run: every changed skill directory (added, modified or renamed files; the same rule as the
agent.skill_changed signal) is scanned in the WORKING tree with
    skillspector scan <abs dir> --no-llm --format json
under one shared timeout (Gate.timeout_s). `--no-llm` is always passed: file contents never go to
an LLM provider. LangSmith/LangChain tracing is forced off. An author-shipped baseline
(.skillspector-baseline.yaml) is never applied (no --use-shipped-baseline).

Egress: SkillSpector's SC4 supply-chain check queries api.osv.dev with the dependency names and
versions of manifests inside the skill even with --no-llm; there is no flag to disable it.
SKILLSPECTOR_OSV_TIMEOUT (default here 10s, a caller's value is kept) bounds it. When OSV cannot
be reached, SkillSpector records an analyzer_runtime_error and marks the analysis incomplete, so
the gate is UNKNOWN. Every result reason states this egress.

Mapping per scanned directory (first match wins):
    tool not started, timed out, killed, exit 2, or exit not in {0, 1}      UNKNOWN
    stdout is not a JSON report                                             UNKNOWN
    version missing or outside the tested range                             UNKNOWN
    execution_successful false (top level or analysis_completeness)        UNKNOWN
    recommendation DO_NOT_INSTALL, or max_issue_severity HIGH/CRITICAL     FAIL (rule ids)
    analysis_completeness.is_complete not true                             UNKNOWN
    recommendation CAUTION (exit 0 hides it)                               UNKNOWN
    recommendation SAFE, complete, exit 0, scanned_components >= 1        PASS
    anything else                                                          UNKNOWN
Findings decide FAIL before completeness: a HIGH finding is negative evidence even when another
file could not be inspected, and a FAIL cannot be waived. The gate result is FAIL if any
directory FAILs, else UNKNOWN if any is UNKNOWN, else PASS.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from repomarshal import git, signals
from repomarshal.model import (
    ChangeSet,
    Cost,
    FileStatus,
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

NAME = "skillspector"
GATE_ID = "skills:audit"
TESTED_LOW: Version = (2, 12, 0)
TESTED_HIGH: Version = (2, 13, 0)
TESTED_RANGE = ">=2.12,<2.13"
SCAN_FLAGS: tuple[str, ...] = ("--no-llm", "--format", "json")
OSV_TIMEOUT_S = "10"
# Forced: tracing would send scanned content to LangSmith.
FORCED_ENV: dict[str, str] = {
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGCHAIN_TRACING": "false",
}
FAIL_SEVERITIES = frozenset({"HIGH", "CRITICAL"})
RECOMMENDATIONS = frozenset({"SAFE", "CAUTION", "DO_NOT_INSTALL"})
EGRESS_NOTE = (
    "egress: SkillSpector SC4 may query api.osv.dev for dependencies declared in the skill "
    "(no opt-out; SKILLSPECTOR_OSV_TIMEOUT bounds it); the LLM stage is off (--no-llm)"
)
MAX_RULES_SHOWN = 12
# Contribute the gate for skills that exist only at BASE (not changed) as well. Off until the
# plan schema's provider enum and the zero-config inventory expectations (lead-owned) accept
# provider gates: skill_audit is triggered only by agent.skill_changed, so a gate for unchanged
# skills would always be SKIPPED and changes no decision or verdict.
DISCOVER_AT_BASE = False


# --------------------------------------------------------------------------- discovery


def changed_skill_dirs(repo: Path, change: ChangeSet | None) -> tuple[str, ...]:
    """Skill directories touched by added, modified or renamed files (both rename ends)."""
    if change is None:
        return ()
    known = signals.base_skill_dirs(repo, change.base)
    dirs: set[str] = set()
    for f in change.files:
        if f.status is FileStatus.DELETED:
            continue
        for p in (f.path, f.old_path):
            if p:
                d = signals.skill_dir(p, known)
                if d is None and signals.symlinked_skill_entry(repo, p):
                    d = p  # scanned as a skill entry; run() reports a symlink as UNKNOWN
                if d is not None:
                    dirs.add(d)
    return tuple(sorted(dirs))


def discover(
    repo: Path, base: str | None, change: ChangeSet | None, path_env: str | None
) -> tuple[Gate, ...]:
    in_change = bool(changed_skill_dirs(repo, change))
    at_base = DISCOVER_AT_BASE and any(
        signals.skill_dir(f) is not None for f in git.list_files_at(repo, base)
    )
    if not in_change and not at_base:
        return ()
    available = which(NAME, {"PATH": path_env} if path_env is not None else None) is not None
    return (
        Gate(
            id=GATE_ID,
            kind=GateKind.SKILL_AUDIT,
            argv=(NAME, "scan", "<skill-dir>", *SCAN_FLAGS),
            cwd="",
            scope="",
            cost=Cost.EXPENSIVE,
            source=f"provider:{NAME} (NVIDIA SkillSpector {TESTED_RANGE})",
            provider=NAME,
            available=available,
            unavailable_reason=None if available else f"{NAME} not found on PATH",
            definition_digest=sha256_json(
                {"provider": NAME, "flags": list(SCAN_FLAGS), "tested": TESTED_RANGE}
            ),
        ),
    )


# --------------------------------------------------------------------------- mapping


def _rule_ids(issues: Any) -> list[str]:
    out: list[str] = []
    if isinstance(issues, list):
        for issue in issues:
            rid = issue.get("id") if isinstance(issue, dict) else None
            if isinstance(rid, str) and rid not in out:
                out.append(rid)
    return out


def _shown(ids: list[str]) -> str:
    text = ", ".join(first_line(i, 40) for i in ids[:MAX_RULES_SHOWN])
    if len(ids) > MAX_RULES_SHOWN:
        text += f" (+{len(ids) - MAX_RULES_SHOWN} more)"
    return text or "none listed"


def report_version(report: Any) -> Version | None:
    meta = report.get("metadata") if isinstance(report, dict) else None
    return parse_version(meta.get("skillspector_version")) if isinstance(meta, dict) else None


def classify(
    code: int | None, stdout: bytes, cli_version: Version | None = None
) -> tuple[ResultStatus, str, Version | None]:
    """(status, reason, version) for one scan; see the module docstring for the table."""
    if code == 2:
        return ResultStatus.UNKNOWN, "SkillSpector error (exit 2); result unknown", None
    if code not in (0, 1):
        return ResultStatus.UNKNOWN, f"unexpected exit code {code}; result unknown", None
    try:
        report = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ResultStatus.UNKNOWN, "output is not a JSON report; result unknown", None
    if not isinstance(report, dict):
        return ResultStatus.UNKNOWN, "output is not a JSON report object; result unknown", None

    version = report_version(report) or cli_version
    if version is None:
        return ResultStatus.UNKNOWN, "SkillSpector version could not be determined", None
    if not in_range(version, TESTED_LOW, TESTED_HIGH):
        return (
            ResultStatus.UNKNOWN,
            f"SkillSpector {version_text(version)} is outside the tested range {TESTED_RANGE}",
            version,
        )

    completeness = report.get("analysis_completeness")
    completeness = completeness if isinstance(completeness, dict) else {}
    if report.get("execution_successful") is not True or (
        completeness.get("execution_successful") is False
    ):
        return ResultStatus.UNKNOWN, "SkillSpector reports execution_successful=false", version

    risk = report.get("risk_assessment")
    if not isinstance(risk, dict):
        return ResultStatus.UNKNOWN, "report has no risk_assessment; result unknown", version
    rec = risk.get("recommendation")
    worst = risk.get("max_issue_severity")
    score = risk.get("score")
    ids = _rule_ids(report.get("issues"))
    summary = f"recommendation {first_line(rec, 40)}, max severity {first_line(worst, 20)}"
    summary += f", score {score}" if isinstance(score, (int, float)) else ""

    if rec == "DO_NOT_INSTALL" or worst in FAIL_SEVERITIES:
        partial = "" if completeness.get("is_complete") is True else " (analysis incomplete)"
        return ResultStatus.FAIL, f"{summary}; rules: {_shown(ids)}{partial}", version
    if completeness.get("is_complete") is not True:
        status = first_line(completeness.get("status"), 30) or "unknown"
        coverage = completeness.get("coverage_percent")
        cov = f", coverage {coverage}%" if isinstance(coverage, (int, float)) else ""
        return (
            ResultStatus.UNKNOWN,
            f"analysis incomplete (status {status}{cov}); {summary}",
            version,
        )
    if rec == "CAUTION":
        return (
            ResultStatus.UNKNOWN,
            f"{summary}; CAUTION is not a pass (exit 0 hides it); rules: {_shown(ids)}",
            version,
        )
    if rec == "SAFE" and code == 0:
        scanned = completeness.get("scanned_components")
        if type(scanned) is not int or scanned < 1:
            # v0.3 fail-open review #6: SAFE over nothing is not evidence about the skill.
            return (
                ResultStatus.UNKNOWN,
                f"{summary}, but {scanned!r} components were scanned: nothing was scanned",
                version,
            )
        return ResultStatus.PASS, f"{summary}; analysis complete, {scanned} component(s)", version
    if rec not in RECOMMENDATIONS:
        return ResultStatus.UNKNOWN, f"unrecognised recommendation {first_line(rec, 40)!r}", version
    return ResultStatus.UNKNOWN, f"{summary} with exit code {code}; inconsistent", version


# --------------------------------------------------------------------------- run


def _needs_cli_version(stdout: bytes) -> bool:
    try:
        report = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(report, dict) and report_version(report) is None


def _cli_version(exe: str, cwd: Path, env: Mapping[str, str]) -> Version | None:
    proc = run_process([exe, "--version"], cwd, env, 60)
    if proc.code != 0:
        return None
    return parse_version(proc.out.decode("utf-8", "replace"))


def run(repo: Path, gate: Gate, change: ChangeSet, env: Mapping[str, str] | None) -> GateResult:
    run_env = dict(os.environ if env is None else env)
    exe = which(NAME, run_env)
    if exe is None:
        return GateResult(
            gate.id, gate.kind, ResultStatus.UNKNOWN, f"unavailable: {NAME} not found on PATH"
        )
    run_env.setdefault("SKILLSPECTOR_OSV_TIMEOUT", OSV_TIMEOUT_S)
    run_env.update(FORCED_ENV)

    root = Path(repo).resolve()
    targets: list[str] = []
    skipped: list[str] = []
    for d in changed_skill_dirs(repo, change):
        path = (root / d).resolve()
        if d and (root / d).is_symlink():
            skipped.append(d)  # a host follows it; what it points to is not the change's content
        elif path.is_relative_to(root) and path.is_dir():
            targets.append(d)
        else:
            skipped.append(d or ".")
    if not targets:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            "no changed skill directory could be scanned (symlink, or not a directory in the "
            f"working tree: {', '.join(skipped) or 'none'}); nothing was scanned",
            argv=gate.argv,
        )

    deadline = time.monotonic() + gate.timeout_s
    statuses: list[ResultStatus] = []
    reasons: list[str] = []
    versions: list[str] = []
    outs: list[bytes] = []
    errs: list[bytes] = []
    summary_lines: list[str] = []
    codes: list[int | None] = []
    duration = 0
    cli_version: Version | None = None
    cli_checked = False
    for d in targets:
        label = d or "."
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            status, reason, version = ResultStatus.UNKNOWN, "not scanned: time budget used", None
        else:
            argv = [exe, "scan", str(root / d) if d else str(root), *SCAN_FLAGS]
            proc = run_process(argv, root, run_env, remaining)
            duration += proc.duration_ms
            outs.append(proc.out)
            errs.append(proc.err)
            codes.append(proc.code)
            if proc.start_error:
                status, reason, version = ResultStatus.UNKNOWN, proc.start_error, None
            elif proc.timed_out:
                status, reason, version = (
                    ResultStatus.UNKNOWN,
                    f"timed out after {gate.timeout_s}s; result unknown",
                    None,
                )
            elif proc.code is not None and proc.code < 0:
                status, reason, version = (
                    ResultStatus.UNKNOWN,
                    f"terminated by signal {-proc.code}; result unknown",
                    None,
                )
            else:
                if not cli_checked and _needs_cli_version(proc.out):
                    cli_checked = True  # the report carries no version: ask the CLI once
                    cli_version = _cli_version(exe, root, run_env)
                status, reason, version = classify(proc.code, proc.out, cli_version)
        statuses.append(status)
        reasons.append(f"{label}: {status.value}: {reason}")
        summary_lines.append(f"[repomarshal] {label}: {status.value}: {reason}")
        if version is not None and version_text(version) not in versions:
            versions.append(version_text(version))

    if ResultStatus.FAIL in statuses:
        final = ResultStatus.FAIL
    elif ResultStatus.UNKNOWN in statuses or skipped:
        # A changed skill that could not be scanned (symlinked, or not a directory) is unaudited.
        final = ResultStatus.UNKNOWN
    else:
        final = ResultStatus.PASS
    if skipped:
        reasons.append(
            "not scanned (symlink, or not a directory in the working tree): " + ", ".join(skipped)
        )
    reasons.append(EGRESS_NOTE)
    label_argv = (NAME, "scan", targets[0] or ".", *SCAN_FLAGS) if len(targets) == 1 else gate.argv
    return GateResult(
        gate.id,
        gate.kind,
        final,
        "; ".join(reasons),
        argv=label_argv,
        exit_code=codes[0] if len(codes) == 1 else None,
        duration_ms=duration,
        stdout_sha256=sha256_bytes(b"".join(outs)),
        stderr_sha256=sha256_bytes(b"".join(errs)),
        output_tail=tail(*errs, "\n".join(summary_lines)),
        tool=f"{NAME} {', '.join(versions)}" if versions else None,
    )
