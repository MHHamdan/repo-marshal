# Threat model

_Phase 4 (MISSION.md §65). The mechanisms are specified in [`../architecture.md`](../architecture.md)
§5–§7 and [`../adr/004-evidence-model.md`](../adr/004-evidence-model.md)._

## What RepoMarshal is and is not

| Control type | RepoMarshal? |
|---|---|
| **Detection** | Yes: deterministic change signals flag risky edits (tests, policy, CI, agent config, auth, secrets) |
| **Verification** | Yes: it re-executes the required gates against an exact tree digest and returns a fail-closed verdict |
| **Enforcement** | Only at one point: a host stop hook can refuse a completion claim. RepoMarshal does not intercept tool calls (AgentWard, Preloop and host permissions do that) |
| **Sandboxing** | **No.** Gates run as the user, in the user's environment. RepoMarshal never claims isolation |

## Adversaries and assets

- **Primary adversary: the coding agent.** It is not necessarily malicious. It is optimising to
  report success, whether through reward hacking, sycophancy or a prompt injection. It runs as the
  same OS user, so it can edit any file, run any command, and call RepoMarshal itself.
- **Secondary adversaries:**
  - malicious repository content: docs, issues or test fixtures that inject instructions;
  - malicious skills or MCP configs;
  - compromised dependencies, of the repository or of RepoMarshal.
- **Assets:**
  - the truthfulness of the verdict (the main one);
  - the integrity of policy and gate definitions;
  - secrets in the environment and in output;
  - the user's trust in a PASS.

## Ranked threats

L = likelihood and I = impact, each scored 1–3. Rank = L × I. The table lists only the v0.1 mitigations
that exist in code, or that are scheduled as v0.1 MUST/SHOULD in the roadmap.

| # | Threat | L | I | Rank | v0.1 mitigation | Later |
|---|---|---|---|---|---|---|
| T1 | **Agent weakens tests** (loosened assertions, mocks that make code trivial) so gates pass | 3 | 3 | 9 | Partial. `tests.changed` puts tests in context and in the evidence record. There is no detection of assertion weakening | AgentProof provider (base/head differential, AP005 assertion weakening); `review` gate |
| T2 | **Agent deletes tests, adds skips, or narrows test discovery** | 3 | 3 | 9 | v0.2: `tests.deleted`, `tests.skip_added` and `tests.discovery_narrowed` taint the affected test gates, so their results are UNKNOWN and the verdict cannot be PASS. Heuristic and documented, with known false positives and negatives | AgentProof base/head differential |
| T3 | **Agent edits a gate's definition** (e.g. `"test": "exit 0"` in package.json, a Makefile target, a pyproject tool section) so the gate trivially passes | 2 | 3 | 6 | The inventory is read at BASE, and the edit fires `build.config_changed`. **Gap:** `npm run test` executes the *working-tree* script body | **`gates.definition_changed`** (SHOULD, priority 1): if a gate's definition differs between BASE and the worktree, its result is UNKNOWN, "re-verify where the definition is trusted" |
| T4 | **Agent edits `repomarshal.toml`, CI workflows or hooks** to weaken or skip checks | 2 | 3 | 6 | Policy is read from BASE only. `policy.self_modified`, `ci.changed` and `agent.config_changed` elevate risk and widen the plan | CI re-runs `done` with the BASE workflow; host-managed (non-repo) hook settings documented |
| T5 | **Agent forges evidence or results** in `.repomarshal/` | 2 | 3 | 6 | Evidence is never a verdict input. There is no cache. A verdict comes only from the same invocation's execution. `record_digest` catches casual edits | Hash chain (SHOULD); HMAC'd cache with key outside the repo; CI as anchor |
| T6 | **Stale evidence**: a verdict reused after the code changed, or edits during the gate run | 2 | 3 | 6 | A tree digest (temporary index + `add -A`) is taken before and after. A mismatch makes the verdict UNKNOWN. Verdicts are bound to plan, policy and base | `status` reports STALE (SHOULD) |
| T7 | **Agent disables the stop hook**, so `done` never runs | 2 | 3 | 6 | `agent.config_changed` is elevated (visible when `done` runs). The v0.2 hook shim fails closed on every error path. Otherwise there is no local defence | Documented: put hooks in host-managed settings, and run `done` in CI |
| T8 | **Task text argues gates away** ("trivial docs fix, skip tests") | 3 | 2 | 6 | By construction, task text can only add signals. Gates are decided from the diff | n/a (structural) |
| T9 | **Malicious skill**, added in the diff or recommended | 2 | 3 | 6 | Skills come only from the repo at BASE. A skill changed in the diff is never recommended and fires `agent.skill_changed`, so `skill_audit` is required. Without a scanner the verdict is UNKNOWN. v0.3: the SkillSpector provider scans changed skill directories with `--no-llm`; an incomplete scan or CAUTION is UNKNOWN | Scanner bypasses are documented (CSA, 2026-06), so a pass is evidence, not proof |
| T10 | **Prompt injection from repository content** steering the agent via context RepoMarshal recommends | 2 | 2 | 4 | The plan contains **paths and reasons only, never file contents**. Repo content can influence decisions only through add-only deterministic signals. Paths are JSON-escaped | Optional content summaries stay excluded unless they are sanitised |
| T11 | **Secrets in outputs** (gate logs, plan, evidence) | 2 | 2 | 4 | Only redacted 40-line tails are stored; streams are hashed. Patterns cover tokens, keys, `Authorization`, and URL passwords. The native `secrets:diff` gate flags added secrets | Provider-grade scanners (gitleaks) as an optional gate |
| T12 | **BASE manipulation** (the agent commits to main locally, or moves refs) so policy comes from its own commit | 1 | 3 | 3 | BASE prefers `origin/*` refs. The base SHA is recorded in plan and evidence | CI derives BASE independently; warn when BASE is a local-only commit |
| T13 | **Running `done` on an untrusted repository** executes its scripts | 1 | 3 | 3 | `plan` never executes repository code. `done --dry-run` lists the commands. Docs say `done` = running the repo's tests | — |
| T14 | **Command injection** via config, paths or task text | 1 | 3 | 3 | Only argv lists, never `shell=True`. Config argv must be an array. Paths and task text are never interpolated into commands | — |
| T15 | **Symlink or path traversal**: a changed symlink pointing outside the repo, or Markdown links to `../../etc` | 1 | 2 | 2 | Content comes from git objects, so a symlink yields only its target string. The docs-links check resolves paths inside the repo root and never reads outside it | — |
| T16 | **Supply chain of RepoMarshal itself** | 1 | 3 | 3 | Zero runtime dependencies. Dev dependencies pinned. CI with pinned actions and least-privilege permissions | Release provenance (trusted publishing) |
| T17 | **Compromised or misbehaving provider tool** | 1 | 3 | 3 | Providers are user-installed and run as subprocesses. Version and digests are recorded. Out-of-range versions give UNKNOWN | — |
| T24 | **Agent grants itself a waiver** (forged evidence, a label it adds, a comment from its token, a local `waive` call) to turn UNKNOWN into PASS | 2 | 3 | 6 | v0.3: waivers are read only from the GitHub API in CI (label + comment by a CODEOWNER/admin/maintainer who is not the PR author or a bot, or an APPROVED CODEOWNER review on the current head), or locally from a TTY with the gate id retyped, bound to the tree digest and ignored in CI. Never from evidence files, the hook or MCP. A FAIL is never waivable. Only counts when BASE policy permits | A local waiver is advisory, like every local PASS; an agent that can drive a pseudo-terminal can fake one locally. CI is the anchor |
| T25 | **Agent exits the Stop-hook loop** by stopping twice | 3 | 2 | 6 | v0.3: a second stop with `stop_hook_active` is allowed only for the same non-PASS verdict over the same evidence digest, and the user is told the change is UNVERIFIED, with the open gates. A new or worse verdict blocks | By design the agent can end its turn unverified; the user is told so, and CI still judges |

### Found by the v0.1 adversarial review (all fixed, each with a regression test in `tests/review/`)

1. An empty change plus a docs task passed: now UNKNOWN.
2. Fast-forwarding local `main` hid committed work: BASE is now remembered.
3. A gate swapped files for later gates: the digest is now checked after every gate.
4. Edits to tool config (`.pytest.ini`, `conftest.py`, nested `ruff.toml`, eslint, tsconfig, `.npmrc`)
   or to scripts a gate runs were not definition changes: they now count (M14).
5. `backend/pytest.py` shadowed `python -m pytest`: gates now run with `PYTHONSAFEPATH=1`.
6. Agent-controlled ignore rules hid new files: only BASE `.gitignore` files are trusted now.
7. An embedded git repository hid its content: it is now opaque, so UNKNOWN.
8. `-diff` attributes or the 2000-line cap hid added lines: diffs use `--text`, and hidden lines
   require the secrets gate.
9. A rename out of a scope skipped the source scope: both paths are now classified.
10. Non-doc files under `docs/`, and `.mdx`, were treated as docs only: they are now classified
    properly.
11. A `root/` scope collided with the root gate ids: the ids are now distinct, and duplicates fail
    closed.

| # | Remaining threat | L | I | Note |
|---|---|---|---|---|
| T18 | Code placed in a self-ignoring tool-cache directory (`.pytest_cache/` and others) | 1 | 2 | Only reachable through a visible change, such as a `sys.path` edit |
| T19 | Git clean/smudge filters or a submodule with `ignore=all`, configured by the agent, altering what `git add` records | 1 | 3 | Not handled in v0.1. CI re-run is the anchor |
| T20 | The agent deletes the remembered BASE (`$GIT_DIR/repomarshal/base.json` since v0.3) *and* moves refs before the first `done` | 1 | 3 | Falls back to automatic BASE. CI with an explicit BASE is the anchor |

### Found by the v0.2 adversarial review (all fixed, each with a regression test in `tests/review_v02/`)

1. **An MCP client or CLI `--base` could narrow the diff or overwrite the remembered BASE.** An
   explicit BASE now only widens (merge-base with the automatic or remembered BASE) and is never
   remembered.
2. **Committing directly on a remote-less default branch moved the automatic BASE with the agent's
   commits.** A `SessionStart` hook (installed by `init --print claude-code`) now anchors BASE
   before the agent works.
3. **A repository nested, untracked, inside the project could be verified instead of the
   project.** `done` refuses it, and the hook prefers `CLAUDE_PROJECT_DIR` over the payload cwd.
4. **SIGTERM or a host timeout during gates made the hook exit with no output,** which allowed the
   stop. It now blocks on SIGTERM, SIGHUP and an overall deadline (`REPOMARSHAL_HOOK_DEADLINE`,
   default 1700 s).
5. **Undecodable stdin crashed the hook outside its fail-closed wrapper.** Stdin is now read and
   decoded inside it.
6. **Taint turned a FAIL into UNKNOWN.** Taint and tree-changed now only downgrade a PASS.
7. **Skip, collection and discovery evasions** (aliases, unicode lookalikes, `__test__ = False`,
   `importorskip`, moves into uncollected locations, `jest.roots`, `pytest.toml`) are detected.
8. **Runner narrowing through `PYTEST_ADDOPTS`, positional paths or `--` passthrough** is detected.

| # | Remaining threat (v0.2) | L | I | Note |
|---|---|---|---|---|
| T21 | The hook is killed with SIGKILL (no handler possible) | 1 | 3 | Claude Code then treats it as a non-blocking error. CI is the anchor |
| T22 | A Stop with no SessionStart anchor, on a default branch with no remote, after the agent committed | 2 | 3 | The committed work is indistinguishable from the baseline locally. Install the SessionStart hook, push to a remote, and rely on CI |
| T23 | Tamper detection is heuristic: Make variables, conftest hooks, `setattr(..., "__test__", False)`, CI `run:` commands | 2 | 2 | Documented false negatives. Some false positives are accepted because the taint fails closed |

### Found by the v0.3 adversarial review (all fixed, each with a regression test in `tests/review_v03/`)

1. **Gate code could rewrite the GitHub event file before waivers were read.** A PR test could
   point `head.sha` at an older, approved commit, or `number` at another PR's waiver. Waivers are
   now collected before the first gate runs, as are BASE policy and CODEOWNERS.
2. **A committed `.repomarshal/state/base.json` could move the CI BASE to an older commit** the PR
   chose, and with it the policy, CODEOWNERS and gate definitions. The anchor state now lives in
   the git directory (`$GIT_DIR/repomarshal/base.json`), which no commit can write.
3. **A waiver could turn "nothing verified" into PASS.** PASS now needs at least one gate that
   actually ran and passed.
4. **A hook state file symlinked to `/dev/zero` could exhaust memory.** The resulting SIGKILL allows
   the stop. State reads no longer follow a final symlink, read only regular files, and are capped
   at 1 MiB (hook and anchor state).
5. **A new skill added as a symlinked directory skipped the skill audit.** A symlink directly in a
   skill root is now a skill change, and an unscanned changed skill makes the audit UNKNOWN.
6. **A SkillSpector SAFE report over zero components mapped to PASS.** It is now UNKNOWN.
7. **`.npmrc` edits were not Playwright definition changes.** Root and scope `.npmrc` now count.
8. **Provider gates received the CI token.** Tokens are now stripped for provider gates as well
   as command gates.
9. **The dogfood job ran the PR's build code (`pip install -e .`) before installing the judge.**
   The judge is now installed first, and the gates' tools come from BASE's requirements.

| # | Remaining threat (v0.3) | L | I | Note |
|---|---|---|---|---|
| T26 | Gates ran the PR's code in the same CI job, as the same user, as the judge | 1 | 3 | **Closed in v0.4** in CI: `repomarshal ci` runs each gate in its own job with no token permissions, and the judge jobs run no PR code. A gate result binds to the plan, HEAD, tree and GitHub's record of its job, or it is UNKNOWN (see the v0.4 section). Locally, gates and the judge still share a user; a local PASS stays advisory |
| T27 | Local waivers are only as strong as the TTY check: an agent that can drive a pseudo-terminal can type the confirmation | 2 | 2 | Local waivers are advisory, like every local PASS. CI ignores them |

### v0.4 changes (each with tests in `tests/unit/test_waiver_identity.py` and `tests/unit/test_ci_split.py`)

1. **Solo-maintainer deadlock.** The PR author could never grant a waiver, and on a one-maintainer
   repository the author is the only CODEOWNER. BASE policy `completion.allow_author_waivers`
   (default false) now allows it.
2. **The agent is indistinguishable from the maintainer.** A coding agent acting through a GitHub
   App under the maintainer's account labels and comments with the maintainer's login. Waivers
   now require `performed_via_github_app: null` on the labeled event and on the reason comment.
   An app value, or a missing or unreadable field, rejects the waiver. Reviews carry no such
   field on any GitHub API, so review waivers are always rejected.
3. **T26 closed in CI** by the split jobs above.

| # | Remaining threat (v0.4) | L | I | Note |
|---|---|---|---|---|
| T28 | The agent holds the maintainer's own credentials outside an app (a PAT, a logged-in `gh` or a browser session) | 1 | 3 | GitHub cannot tell those actions from the maintainer's. Keep such credentials away from agent environments |
| T29 | With `allow_author_waivers`, anyone who can act as the author in the web UI can waive | 1 | 2 | That is the maintainer by definition on a solo repository. Keep it off where a second maintainer exists |
| T30 | A gate job lies about its own result | 2 | 2 | As in any CI: the PR's code decides its own exit code. The split stops one gate from affecting another gate, the plan, the waivers or the verdict |

### Found by the v0.4 adversarial review (all fixed, each with a regression test in `tests/review_v04/`)

1. **The judge jobs executed the PR's code during setup** (critical). `python -m venv`,
   `python -m pip` and `python - <<PY import tomllib` ran with the PR checkout as the working
   directory, so a PR file `venv/`, `pip/` or `tomllib.py` ran first and could install a fake
   judge that printed PASS. Every judge-side Python call now uses `-I`, the setup steps run in
   `$RUNNER_TEMP`, and a static test enforces both.
2. **A real FAIL could be made waivable.** Another gate job could upload a forged UNKNOWN under
   the failed gate's artifact name, or take that name first. The status now comes from GitHub's
   step record (`run gate` fails exactly on FAIL; `gate passed` succeeds exactly on PASS), and a
   recorded FAIL stays FAIL with or without a usable file.
3. **Binding problems did not block PASS when only native gates were required.** They now force
   UNKNOWN, and waivers cannot lift them.
4. **A missing `GITHUB_RUN_ATTEMPT` defaulted to attempt 1.** It is now an error.
5. **An edited reason comment still counted.** `performed_via_github_app` describes who created
   the comment, not who edited it. Edited comments are rejected.
6. **A waiver label no longer on the PR could still grant.** Renamed or deleted labels left no
   matching `unlabeled` event. The label must now also be on the PR right now, compared without
   regard to case.
7. **A waiver line quoted in a code block or block quote counted as a reason.** Only prose lines
   count now.

| # | Remaining threat (v0.4) | L | I | Note |
|---|---|---|---|---|
| T31 | **The workflow file comes from the PR.** On `pull_request` events GitHub runs the PR's own `ci.yml`, so a PR can rewrite or remove the rm-plan, rm-gate and dogfood jobs | 2 | 3 | **Closed in v0.5**, with one setup step. The verdict comes from `repomarshal-verdict.yml` (`workflow_run`, always the default-branch copy). It refuses gate records when the PR changed `ci.yml`, binds them to the PR head SHA, and posts the commit status `repomarshal/verdict`. A PR that removes or renames `ci` leaves the required status missing. Forgery of the status itself is closed only when the status is posted by a GitHub App whose key is in a default-branch-only environment and branch protection pins the check to that App (T35) |
| T32 | A gate's own PR code can rewrite its result file (including `tree_after`) after `repomarshal ci gate` writes it and before upload | 2 | 1 | Inside T30: it lies only about itself, and the status comes from GitHub's step record |
| T34 | A `push` run on a PR branch adds a skipped `dogfood` check (skipped counts as success) to the PR head commit, next to the PR run's real verdict | 2 | 3 | Fixed during PR #3 CI: `push` now runs on `main` only, guarded by a static test |
| T33 | Waiver labels persist across new pushes | 2 | 2 | Unchanged since v0.3. Remove `repomarshal-waive:*` labels once they no longer apply |

### v0.5: the trusted verdict

| # | Remaining threat (v0.5) | L | I | Note |
|---|---|---|---|---|
| T35 | Without the GitHub App setup, any workflow on any branch can post `repomarshal/verdict` with the workflow token | 3 | 3 | The token's identity is the same for every workflow. Fix: App plus environment plus pinned source (ci.md, "Who may post that status"). The trusted workflow uses the App automatically once its secrets exist |
| T36 | An agent with admin rights (for example through an app acting as the owner) changes branch protection, the environment, or pushes to the default branch | 1 | 3 | Outside what a workflow can enforce. Turn on "Do not allow bypassing", and do not give agents administration permission |
| T37 | The tested commit is the PR head, not GitHub's merge commit with the latest base | 2 | 1 | The plan uses the merge base. Turn on "Require branches to be up to date before merging" to re-verify after the base moves |

## Residual risks that must be stated in the README

1. **A local PASS is advisory.** A same-user agent can, with deliberate effort, defeat any local
   check, for example by editing the RepoMarshal installation itself. The authoritative verdict comes
   from re-running `done` where the agent cannot write: CI or a reviewer's machine.
2. **Test quality is not measured.** RepoMarshal proves which commands ran and how they exited, not
   that the tests are meaningful (T1).
3. **Ignored files** (`.env`, build outputs) are outside the tree digest.
4. **Signal coverage is heuristic.** Unknown files widen the plan rather than shrink it, but a risky
   change hidden in an innocuous-looking file can be under-classified. The gate floor (cheap and
   standard gates always run in a changed scope) bounds the damage.

## Priorities derived from this model

These feed the roadmap SHOULD list, in this order:
1. `gates.definition_changed` (T3)
2. test-tamper signals (T2)
3. the CI recipe for re-running `done` (T4, T5, T7)
4. the SkillSpector adapter (T9)
5. AgentProof integration (T1)
