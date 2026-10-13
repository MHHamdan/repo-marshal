# Research notes (evidence appendix)

These are the raw Phase 1 working notes behind `../ecosystem-comparison.md`,
`../gap-analysis.md` and `../../UPSTREAM_PROVENANCE.md`. They hold the `path:line`
citations that back the condensed documents.

- **How they were produced:** each upstream repository was cloned read-only into a temporary
  research directory (`<research-clones>/`) at the SHA recorded in each note.
- **Labels:** **VERIFIED** (seen in a file or observed by running the tool), **INFERRED**, **UNKNOWN**.
- **Not inspected:** the GitHub REST API and issue pages were unreachable from the research
  environment, so upstream issues, release pages and star counts were not inspected unless a
  note says otherwise.
- **Status:** the notes are a snapshot from 2026-10-13. They are not maintained documentation.

| File | Covers |
|---|---|
| `security-runtime.md` | NVIDIA SkillSpector, Reticle, Playwright |
| `context-skills.md` | Chisel, UI Skills, UI/UX Pro Max |
| `competitors-verification.md` | AgentProof, AgentSmith, DoneProof, AgentWard |
| `agentrig-mcp.md` | doidor/agentrig, regenrek/agentrig, the MCP specification, agent host surfaces |
| `preloop.md` | Preloop's diff-selected verification gate (PR #435) |
| `additional-discoveries.md` | Web sweep: other prior art and the name-collision check |
