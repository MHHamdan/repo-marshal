# RepoMarshal — Session Status

_Last updated: 2026-10-13_

## Current phase

**Phase 1 — Research** (MISSION.md §62). No implementation scaffolding yet.

Done in this phase:
- Read MISSION.md and CLAUDE.md.
- Cloned every upstream repo named in MISSION.md §4–§6 read-only into the session
  scratchpad (blobless clones; Playwright sparse) and recorded HEAD SHAs (below).
- Started deep per-repo research (README, licenses, CLI, schemas, tests, tags) and
  a web search for additional prior art and name collisions.

## Next step

Write `docs/research/ecosystem-comparison.md`, `docs/research/gap-analysis.md`
and `docs/UPSTREAM_PROVENANCE.md` from the research notes, then commit. If resuming
locally without the notes (they lived in the cloud scratchpad), re-clone the repos
at the SHAs below and re-run the per-repo method in MISSION.md §61.

## Upstream repos inspected

| Repository | Commit SHA | Last commit (UTC offset as recorded) |
|---|---|---|
| NVIDIA/SkillSpector | 69d7d059da38160296b2311320ed4c72a9c3fdb6 | 2026-10-12T21:40:43+05:30 |
| reticlehq/reticle | 1a337fa254466c33fb5888ddc0e269e1811eb2f9 | 2026-10-13T04:35:58+00:00 |
| chisel-sh/chisel | f5a2cc470a240d6ad521be5006d117ef252d581b | 2026-09-09T12:28:36-05:00 |
| ibelick/ui-skills | f2264c30213a8a55bd0a6a7668d0438e4564e090 | 2026-10-12T18:28:03+02:00 |
| nextlevelbuilder/ui-ux-pro-max-skill | 7246805992a42b7bba37d4cac83b01416f0c6c44 | 2026-10-12T18:08:03+07:00 |
| GenRamzi/AgentProof | 7a748e9820bc15e4182aee92c16be5160e2be7e4 | 2026-08-15T11:43:17+00:00 |
| agentward-ai/agentward | 0e049066a8f4b48196803e330bc2f0a54bb2eda4 | 2026-04-28T22:34:53-05:00 |
| PromptPartner/agentsmith | 0901316e730c4aca7a2aabe1562c4d63b1ea0444 | 2026-10-12T00:58:24+02:00 |
| doidor/agentrig | 05d0dd5e8c8276faa57d786080dc3b18c6c74d7b | 2026-10-02T14:11:59+02:00 |
| regenrek/agentrig | 50b6983d2f6264511062be4f1b8877615f79639b | 2026-08-13T16:13:31+02:00 |
| giugiu-a11y/doneproof | 4a45ebc01fb41c33130657e8addd15f4c9bcc4d4 | 2026-08-23T01:37:33-03:00 |
| modelcontextprotocol/modelcontextprotocol | 3778656b2e0ba6725035e816fe3085374cbaa736 | 2026-10-12T09:26:31-07:00 |
| microsoft/playwright | a3401cfe0dcf317101572d025fa46248d0779b42 | 2026-10-12T16:44:45-07:00 |

## Open questions / blockers

- **No git remote is configured** in the cloud checkout (`git remote -v` is empty),
  so commits cannot be pushed from this session. Work is committed locally on
  `claude/phase1-research`. The user must add the remote, or push this branch
  from a session where `MHHamdan/repo-marshal` is attached.
- The GitHub REST API and github.com issue pages return 403 from this environment,
  so upstream **issues, releases pages and star counts were not inspected** via
  GitHub. Git tags were inspected instead.

## Decisions

- Work branch `claude/phase1-research` (session started on `main`; CLAUDE.md
  forbids working directly on the default branch).
- Name check: `repomarshal` is unclaimed on PyPI and npm (both returned 404 on
  2026-10-13). A wider collision search is still in progress.
