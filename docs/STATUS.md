# RepoMarshal — Session Status

_Last updated: 2026-10-13_

## Current phase

**Phase 6 (the vertical slice, MISSION.md §67) is in progress** on branch `claude/v0.1-slice`,
which was branched from `origin/main` at 8a5f995. Phases 1–5 are merged to main. The scope change
from the owner: M14 (`gates.definition_changed` → UNKNOWN) is now v0.1 MUST.

Working model: the lead owns the contract and integration, and five agents work in parallel, each
in its own git worktree with its own branch.

| Agent | Branch | Items | Owns | Status |
|---|---|---|---|---|
| git | `slice/git` | M1, M2 | `src/repomarshal/git.py`, `tests/unit/test_git.py` | **merged** |
| inventory | `slice/inventory` | M3, M4, M14 | `inventory.py`, `policy.py`, `tests/unit/test_inventory.py`, `tests/unit/test_policy.py` | **merged** |
| planner | `slice/planner` | M5, M6, M7 | `signals.py`, `decisions.py`, `native.py` (run_* only), `tests/unit/test_signals.py`, `test_decisions.py`, `test_native.py` | **merged** |
| context | `slice/context` | M8, M9 | `context.py`, `skills.py`, `tests/unit/test_context.py`, `test_skills.py` | **merged** |
| review | `slice/review` | Step 4 | `tests/review/**` only (fail-open hunt) | **merged**: 11 confirmed fail-open paths, 22 red `failopen` tests |
| fix-git | `fix/git` | review #6, #7, #8 | `git.py`, `tests/unit/test_git.py` | in progress |
| fix-signals | `fix/signals` | review #8, #9, #10 | `signals.py`, `tests/unit/test_signals.py` | **merged** |
| fix-inventory | `fix/inventory` | review #4, #11 | `inventory.py`, `tests/unit/test_inventory.py` | **merged** |
| fixture | `slice/fixture` | M13 | `tests/fixtures/**`, `tests/acceptance/**` | **merged** |

The lead owns `model.py`, the schemas, `planner.py`, `runner.py`, `verdict.py`, `evidence.py`,
`api.py`, `cli.py` and `tests/helpers.py`.

Done:
- **All five agent branches are merged.** The integration fixes: binding plans are compiled from the
  diff alone (ADR-005), and stylesheets are exempt from `security.auth_path`.
- Full suite: 364 passed, 1 skipped (root cannot create an unreadable file). Ruff check and format
  are clean.
- `README.md`, with the real demo output for Scenarios A, B and C (A PASS, B PASS, C UNKNOWN).
- The lead's modules are committed with unit tests: `runner.py` and `verdict.py` (fail-closed),
  plus `planner.compile_plan`, `api.plan` / `api.done`, `evidence.py`, `cli.py` and `render.py`.
  They are wired to the agent modules, so they work end to end once those are merged.
- The contract is committed: `pyproject.toml` (stdlib-only core; dev deps pytest, jsonschema and
  ruff), `src/repomarshal/model.py` (IR dataclasses), JSON Schemas (M12), interface stubs with the
  behaviour spec in each docstring, `tests/helpers.py` and `tests/unit/test_contract.py`.

## Next step

Lead fixes #1, #2, #3 and #5 are committed: 5 of the 22 failopen tests are green, with BASE
anchoring in `src/repomarshal/anchor.py`. Known red until fix/git merges: the guard
`test_exclude_everything_cannot_hide_tracked_edits` (an ignored `.repomarshal/` breaks `git add`;
the fix is assigned to fix-git).

The fail-open review is merged. `pytest -m failopen` shows 22 tests that must turn green. The fixes
are split up as follows:
- **Three fix agents** work on the branches in the table: git ignore rules, embedded repos and
  `--text` diffs; signals for renames, docs classification and hidden lines; inventory for tool
  configs, delegated files and root-scope ids.
- **The lead** fixes:
  - #1: an empty change is UNKNOWN;
  - #2: BASE is remembered in `.repomarshal/state/base.json` and combined with the automatic BASE
    via merge-base, so a moved ref can only widen the diff;
  - #3: the tree digest is checked after every gate;
  - #5: `PYTHONSAFEPATH=1` for gates;
  - #11: duplicate gate ids fail closed;
  - `ChangeSet.opaque` forces UNKNOWN.

The suite must then be fully green, with `-m failopen` included, before the owner is told the exit
criteria pass.

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
