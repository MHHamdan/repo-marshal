# RepoMarshal — Session Status

_Last updated: 2026-10-13_

## Current phase

**v0.2 is complete** on branch `claude/v0.2` and is waiting for the owner to verify and merge.
Phases 1–5 and v0.1 are merged to main.

Delivered (roadmap SHOULD #2, #4, #5, #6, #7, plus `ci.changed` and `agent.config_changed`):
- **Test-tamper signals:** `tests.deleted`, `tests.skip_added` and `tests.discovery_narrowed` taint
  test gates, so their results are UNKNOWN.
- **CI:** `.github/workflows/ci.yml` runs pytest on 3.11–3.13, ruff, the MCP extra job, and a PR
  dogfood `done`. Actions are pinned by SHA and run with least privilege. The recipe is in
  `docs/guides/ci.md`.
- **Commands:** `init`, `doctor`, and the Claude Code hook `done --hook claude-code`
  (SessionStart anchors BASE; Stop fails closed).
- **MCP:** a stdio server as the `repomarshal[mcp]` extra (plan, done, explain; MCP 2026-07-28 with
  2025-11-25 fallback).
- **Review:** a fresh fail-open review found 10 paths. All are fixed, and the tests are in
  `tests/review_v02/`.

Measured on 2026-10-13:
- **Suite:** 830 passed, 14 skipped (13 SDK-only MCP tests, 1 root-only). With the SDK venv,
  `tests/review_v02`, the MCP tests and the contract tests give 88 passed. Ruff is clean.
- **`plan` cold start:** median 181–184 ms, max 228 ms (v0.1: 179–188 ms). The MCP extra adds
  nothing to `plan`.
- **Demo verdicts unchanged:** A PASS, B PASS, C UNKNOWN.

Local branches: `claude/v0.2` only, plus the old `claude/phase1-research` and `claude/v0.1-slice`.
All v0.2 agent worktrees and branches are removed. Remote branches are left to the owner.

## Next step

The owner verifies and merges `claude/v0.2`. Then, in roadmap SHOULD order: the remaining
catalogue signals (`agent.skill_changed`, `dependency.changed`, `db.migration`), the SkillSpector
and Playwright adapters, more inventory sources, the evidence hash chain with `status`, waivers,
and publishing to PyPI.

Repository settings for the owner, which can't be done from here:
- make `ci / dogfood` a required check;
- add CODEOWNERS for `.github/workflows/`.

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
| preloop/preloop (discovered) | 71dd091e8e6e7503a8105b3b18b772df9707e15c (feature merge c762ff2a) | 2026-10-13T03:13:37+02:00 |

## Open questions / blockers

- Resolved: the cloud checkout started with no git remote. `MHHamdan/repo-marshal` is now
  attached with push access, `origin` is added, and `claude/phase1-research` is pushed. The
  commits were rebased onto `origin/main` and their author reset to the owner identity, as
  required by the commit-identity check.
- The GitHub REST API and github.com issue pages return 403 from this environment,
  so upstream **issues, releases pages and star counts were not inspected** via
  GitHub. Git tags were inspected instead.

## Decisions

- Work branch `claude/phase1-research` (session started on `main`; CLAUDE.md
  forbids working directly on the default branch).
- **Keep the name RepoMarshal.** It is unclaimed on PyPI, npm, crates.io and GitHub. Avoid a bare
  `marshal` CLI, package or import: chiruu12/marshal, crates.io `marshal` and the Python standard
  library all use it.
- **Diff-driven check selection is not novel.** Preloop PR #435 already does it with path globs. We
  claim the combination: inferred gate inventory, content signals, reasons for skipped gates, one
  plan from before the work to the verdict, and a typed five-state verdict.
- **Policy is loaded from the BASE commit.** Edits to the policy, CI or agent config are high-risk
  signals, so an agent cannot relax its own requirements.
- **Only gates RepoMarshal itself ran count as evidence.** Agent-reported results are claims.
- **Minimise only expensive gates.** Cheap gates always run, and uncovered files get a conservative
  default. CI stays authoritative. This answers AgentSmith rule R5.
- **No generation of agent-specific config.** The agent-facing interface is MCP stdio plus the CLI.
  Hook shims start with Claude Code `Stop`.
- **v0.1 real adapters:** SkillSpector (security) and Playwright (runtime). Reticle, Chisel, UI Skills,
  UI/UX Pro Max and AgentProof are PLANNED, with no stubs.
- **License: Apache-2.0**, decided by the owner on 2026-10-13.
- **Task text can only add requirements, never remove them.** The diff dominates, and the plan is
  recompiled from the actual diff at completion time.
- **Verdict precedence is BLOCKED > FAIL > UNKNOWN > PASS.** Exit codes are 0 for PASS, 1 for FAIL,
  2 for UNKNOWN and 3 for BLOCKED. SKIPPED, NOT_APPLICABLE and WAIVED are never evidence.
- **Pulling `main` into this branch uses merge, not rebase.** The branch is already pushed, and
  CLAUDE.md forbids rewriting pushed history.
