"""Named gate providers (owner: agent "adapters"; ADR-002; roadmap SHOULD #8, #9).

    discover(repo, base, change, path_env) -> tuple[Gate, ...]
        Extra gates contributed by providers, read at BASE like every other gate definition
        (e.g. a `skill_audit` gate backed by SkillSpector when SKILL.md files exist; a `runtime`
        gate backed by Playwright when playwright.config.* and @playwright/test exist).
        Gate.provider names the adapter ("skillspector", "playwright"). Unavailable tools give
        available=False with a reason, never a fake gate.
    run(repo, gate, change, env) -> GateResult
        Execute the named provider. Must parse the tool's machine output, map its incomplete or
        uncertain states to UNKNOWN, record GateResult.tool ("<name> <version>"), refuse
        versions outside the tested range (UNKNOWN), and pass privacy opt-outs.

Integration status is explicit (ADR-002): an adapter that is not implemented has NO entry here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from repomarshal.model import ChangeSet, Gate, GateResult, ResultStatus

PROVIDERS: tuple[str, ...] = ()  # names of implemented adapters


def discover(
    repo: Path, base: str | None, change: ChangeSet | None, path_env: str | None
) -> tuple[Gate, ...]:
    return ()


def run(repo: Path, gate: Gate, change: ChangeSet, env: Mapping[str, str] | None) -> GateResult:
    return GateResult(
        gate.id,
        gate.kind,
        ResultStatus.UNKNOWN,
        f"provider {gate.provider!r} is not supported in this version",
    )
