# RepoMarshal — Session Status

_Last updated: 2026-10-14_

## Current phase

**v0.8 is in review: PR #7** (`claude/v0.8`), stacked on PR #6 (v0.7, `claude/v0.7`). Neither is
merged. On PR #6 the owner has not yet added the waiver comment or labels, so there is no live
waiver result yet.

v0.8 closes **T40** (T36 and T37 were already used in `docs/security/threat-model.md`): a local
agent using the owner's `gh` login or PAT makes label and comment events with
`performed_via_github_app: null`, so it could self-grant author waivers.

- `src/repomarshal/waiversig.py`: SSH-signed waiver codes. The signed message binds repository,
  PR, head SHA, target, reason and `signed-at`. `ssh-keygen -Y verify`, namespace
  `repomarshal-waiver`, allowed-signers file at BASE, principal = the commenter's login.
- `completion.waiver_signers` (BASE path). When set, every label waiver needs
  `repomarshal-waive-sig <target> <signed-at> <sig>`, posted within 30 minutes of `signed-at` and
  signed after the label's last removal. Author waivers require it (deliberate breaking change).
- `repomarshal waive-sign` prints the comment.
- This repository: `waiver_signers = ".github/repomarshal_allowed_signers"`. The file has only
  comments until the owner adds their public key line, so author waivers are rejected after merge
  until then.
- Fresh fail-open review (`tests/review_v08/`, 30 tests). It found one fail-open (a withdrawn
  signed waiver could be revived by a relabel or a reposted copy), fixed with `signed-at`, plus
  hardening.
- Local: 1575 passed, 18 skipped; ruff clean.
- CI on PR #7 head 548b819: lint, mcp, test 3.11/3.12/3.13, rm-plan and rm-gate-0 pass.
  rm-gate-1 (`root:unit`) is UNKNOWN because its gate definition changed (new
  `tests/review_v08/conftest.py`). dogfood and `repomarshal/verdict` are UNKNOWN with `root:unit`,
  `integration` and `dep_audit` open, as predicted by the local run of main's judge.

## Next step

**v0.9 is prepared** on `claude/v0.9` (pushed, no PR yet), based on `claude/v0.8`:
- `CLAUDE.md`: never post PR comments, reviews or issues on GitHub; report in chat (owner's rule,
  2026-10-16).
- `repomarshal doctor` reports the waiver setup (policy.waivers) and flags weak signed-code
  configurations (tests in `tests/unit/test_doctor.py`).
- Local: 1585 passed, 18 skipped. The v0.8 judge, run with BASE = `claude/v0.8`, gives UNKNOWN
  with one open item: `integration` (required because `agent.config_changed` + `code.changed`
  make the risk elevated; no integration gate). That is the target for the first live signed
  waiver.

Waiting on the owner:
1. Merge PR #6, then PR #7.
2. Commit a key line to `.github/repomarshal_allowed_signers` on main (hardware key; check with
   `repomarshal doctor`).

Then:
3. Merge `origin/main` into `claude/v0.9` (a merge commit: the branch is pushed, so no rebase),
   push, open the v0.9 PR, and give the owner the exact `repomarshal waive-sign` command for
   target `integration` on the PR's head.
4. Record the live result of both judges (advisory dogfood and `repomarshal/verdict`) here.
5. Then ship 0.1.0 (`docs/guides/release.md`).

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
- **v0.3 dogfood ordering:** the judge is installed from BASE before any PR code runs, and gate
  tools come from BASE's dev requirements. A PR build backend could otherwise tamper with git
  state before the judge reads BASE.
- **Waivers are read before gates:** gates run PR code as the same user, so nothing the judge
  trusts is read after the first gate.
- **v0.4 review waivers are rejected outright:** no GitHub API reports whether a review was
  made through an app, and the owner required failing closed when the field is missing.
- **v0.4 one job per gate:** PR code in one job can read the runner's artifact token. Each
  result is therefore cross-checked against GitHub's step conclusions for its own job, which
  PR code in another job cannot change.
- **v0.5 tests the PR head, not GitHub's merge commit:** the trusted verdict binds to the head
  SHA, where the commit status lives. Base drift is handled by "Require branches to be up to
  date" (T37).
- **v0.5 `ci-workflow` waiver:** a PR that changes `ci.yml` cannot bind on its own. A reviewed
  workflow change needs a person's waiver, otherwise a solo maintainer could never merge one
  without bypassing protection.
