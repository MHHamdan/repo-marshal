# ADR-003: Agent interoperability

Status: accepted (2026-10-13)

## Context

RepoMarshal must work with Claude Code, Codex, Cursor, Copilot and Gemini CLI. The research
(`docs/research/notes/agentrig-mcp.md`) found:
- The only common denominator is an **MCP stdio server**.
- Hook formats are not standardised.
- Generating per-host config is an unstable area: doidor/agentrig removed its compiler on 2026-10-01,
  and regenrek/agentrig hard-cut to v2.
- MCP 2026-07-28 is stateless and deprecates Roots.

## Decision

- **Surface 1: the CLI.** Every command has `--json` output that matches the published schema, and
  exit codes carry the verdict (0 PASS, 1 FAIL, 2 UNKNOWN, 3 BLOCKED, 4 internal error).
- **Surface 2: MCP stdio (SHOULD).** `repomarshal mcp` exposes three tools, all taking an explicit
  `repo_path` and returning `structuredContent` with an `outputSchema`:
  - `repomarshal_plan`: read-only;
  - `repomarshal_done`: not read-only, because it runs repository commands;
  - `repomarshal_explain`: read-only.

  The official Python MCP SDK is an optional extra (`repomarshal[mcp]`), imported lazily. Target
  spec: 2026-07-28, with 2025-11-25 as fallback.
- **Surface 3: thin hook shims.**
  - `repomarshal done --hook claude-code` reads the Stop-hook JSON from stdin and returns a block
    decision with the failing or unknown gates when the verdict is not PASS. This is the first shim.
  - Other hosts get shims only when a user needs them.
- **No config ownership.** RepoMarshal never writes `CLAUDE.md`, `AGENTS.md`, `.cursor/`, `.mcp.json`
  or settings files. `repomarshal init --print <host>` prints the snippet to paste.
- **Distribution (later):** one Agent Skills `SKILL.md` ("call repomarshal_plan before editing;
  done before claiming completion") plus an Agent Plugins `mcp.json`. Tools like regenrek/agentrig
  can project these into hosts.
- **No host-specific assumptions in the core:** the IR and the planner know nothing about any agent.

## Alternatives

- **Generate each host's config (an AgentAdapter per host).** Rejected: it is a crowded, churning area,
  it duplicates AgentRig, and it means mutating user files.
- **A proprietary protocol.** Rejected: MCP is sufficient (MISSION.md §6.1).
- **MCP only, without a CLI.** Rejected: hooks and CI need a process with exit codes.

## Consequences

- Each extra host costs a small shim plus documentation, not an adapter framework.
- The MCP surface follows a fast-moving spec. It stays behind an optional extra, so the core works
  if it breaks.
