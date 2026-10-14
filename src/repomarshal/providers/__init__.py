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

    adapter        kind         status        tested range         module
    skillspector   skill_audit  EXPERIMENTAL  >=2.12,<2.13         providers/skillspector.py
    playwright     runtime      EXPERIMENTAL  >=1.50,<1.65         providers/playwright.py

Both run a user-installed tool as a subprocess (argv list, process group killed on timeout) and
never raise: run() turns any unexpected error into UNKNOWN (fail closed).

superseded(repo, base, gates) -> frozenset[str]
    Ids of command gates that a provider gate replaces (a package.json runtime script that only
    runs `playwright test` in a scope with a Playwright provider gate). The inventory may drop
    them to avoid running the same suite twice; keeping them is safe (both must pass).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from repomarshal.model import ChangeSet, Gate, GateResult, ResultStatus
from repomarshal.providers import playwright, skillspector

_ADAPTERS: dict[str, ModuleType] = {
    playwright.NAME: playwright,
    skillspector.NAME: skillspector,
}
PROVIDERS: tuple[str, ...] = tuple(sorted(_ADAPTERS))  # names of implemented adapters


def discover(
    repo: Path, base: str | None, change: ChangeSet | None, path_env: str | None
) -> tuple[Gate, ...]:
    gates: list[Gate] = []
    for name in PROVIDERS:
        gates.extend(_ADAPTERS[name].discover(repo, base, change, path_env))
    return tuple(gates)


def run(repo: Path, gate: Gate, change: ChangeSet, env: Mapping[str, str] | None) -> GateResult:
    adapter = _ADAPTERS.get(gate.provider)
    if adapter is None:
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"provider {gate.provider!r} is not supported in this version",
        )
    try:
        return adapter.run(repo, gate, change, env)
    except Exception as e:  # noqa: BLE001 - fail closed: never let a crash look like success
        return GateResult(
            gate.id,
            gate.kind,
            ResultStatus.UNKNOWN,
            f"internal error in provider {gate.provider!r}: {type(e).__name__}: {e}",
            argv=gate.argv,
        )


def superseded(
    repo: Path, base: str | None, gates: tuple[Gate, ...] | list[Gate]
) -> frozenset[str]:
    return playwright.superseded(repo, base, gates)
