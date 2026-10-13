# ADR-002: Provider integration

Status: accepted (2026-10-13)

## Context

RepoMarshal should use existing tools (SkillSpector, Playwright, Reticle, AgentProof, test runners)
rather than reimplement them. Phase 1 research (`docs/research/notes/security-runtime.md`) found:
- None of them offers a stable, versioned library API.
- None of their JSON outputs carries a schema version.
- Exit codes conflate states: SkillSpector's CAUTION exits 0, and Reticle's `verify` exit 1 covers
  both a real failure and a setup error.
- Licences differ: the Reticle server is FSL, Chisel is FSL and AgentWard is BUSL.

MISSION.md §50 forbids fake integrations.

## Decision

- **One gate abstraction.** Every gate has a `provider`. v0.1 implements only the `command` provider:
  run argv, where exit 0 → PASS, non-zero → FAIL, and a timeout or failure to start → UNKNOWN. Named
  providers are later subclasses of the same interface, `run(gate, ctx) -> GateResult`.
- **Integration is by subprocess, against a user-installed tool.**
  - Never import the tool as a library.
  - Never vendor or bundle it.
  - Never add it as a pip dependency.
- **Each named adapter must:**
  - parse the tool's machine output, not just its exit code;
  - map the tool's own "incomplete" or "unsure" states to UNKNOWN. For example, SkillSpector's
    `analysis_completeness.is_complete == false` maps to UNKNOWN, and Playwright with
    `expected == 0` maps to UNKNOWN;
  - record the tool version in evidence;
  - declare a tested version range, and report UNKNOWN (not PASS) outside it;
  - pass the tool's privacy opt-outs (`--no-llm`, `RETICLE_TELEMETRY=0`).
- **Integration status is explicit, in code and docs:** SUPPORTED, EXPERIMENTAL, PLANNED or
  UNAVAILABLE.
  - A PLANNED provider has **no class at all**. There are no stubs.
  - A missing tool makes its gates UNAVAILABLE, which counts as UNKNOWN in the verdict.
- **Order:** SkillSpector (skill_audit) and Playwright (runtime JSON reporter) are the first named
  adapters (roadmap: SHOULD). Reticle, AgentProof, UI/UX Pro Max and the Chisel reader are MAY,
  after the core is proven.

## Alternatives

- **Import providers as Python libraries.** Rejected: there are no stable APIs, the dependency
  weight is heavy (SkillSpector's venv is 244 MB), and Reticle's licence is a concern.
- **Call providers over MCP.** Rejected for evidence: MCP tools return agent-oriented output (for
  example, Playwright's `test_run` returns plain text), and they need a session. MCP is RepoMarshal's
  interface *to* agents, not *to* providers.
- **Generic exit-code wrapping only.** Kept as the base `command` provider. It is insufficient for
  tools whose exit codes hide UNKNOWN, which is why named adapters exist.

## Consequences

- Adapters break when an upstream changes its output format. Version-range checks turn that into
  UNKNOWN rather than a wrong PASS. Upstream contributions (adding a `schema_version`, separating
  exit codes) are the long-term fix.
- Users install providers themselves. `doctor` (SHOULD) reports which ones are available.
