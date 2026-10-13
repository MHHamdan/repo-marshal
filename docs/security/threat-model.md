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
| T9 | **Malicious skill**, added in the diff or recommended | 2 | 3 | 6 | Skills come only from the repo at BASE. A skill changed in the diff is never recommended and fires `agent.skill_changed`, so `skill_audit` is required. Without a scanner the verdict is UNKNOWN | SkillSpector adapter (SHOULD). Even then, scanner bypasses are documented (CSA, 2026-06), so a pass is evidence, not proof |
| T10 | **Prompt injection from repository content** steering the agent via context RepoMarshal recommends | 2 | 2 | 4 | The plan contains **paths and reasons only, never file contents**. Repo content can influence decisions only through add-only deterministic signals. Paths are JSON-escaped | Optional content summaries stay excluded unless they are sanitised |
| T11 | **Secrets in outputs** (gate logs, plan, evidence) | 2 | 2 | 4 | Only redacted 40-line tails are stored; streams are hashed. Patterns cover tokens, keys, `Authorization`, and URL passwords. The native `secrets:diff` gate flags added secrets | Provider-grade scanners (gitleaks) as an optional gate |
| T12 | **BASE manipulation** (the agent commits to main locally, or moves refs) so policy comes from its own commit | 1 | 3 | 3 | BASE prefers `origin/*` refs. The base SHA is recorded in plan and evidence | CI derives BASE independently; warn when BASE is a local-only commit |
| T13 | **Running `done` on an untrusted repository** executes its scripts | 1 | 3 | 3 | `plan` never executes repository code. `done --dry-run` lists the commands. Docs say `done` = running the repo's tests | — |
| T14 | **Command injection** via config, paths or task text | 1 | 3 | 3 | Only argv lists, never `shell=True`. Config argv must be an array. Paths and task text are never interpolated into commands | — |
| T15 | **Symlink or path traversal**: a changed symlink pointing outside the repo, or Markdown links to `../../etc` | 1 | 2 | 2 | Content comes from git objects, so a symlink yields only its target string. The docs-links check resolves paths inside the repo root and never reads outside it | — |
| T16 | **Supply chain of RepoMarshal itself** | 1 | 3 | 3 | Zero runtime dependencies. Dev dependencies pinned. CI with pinned actions and least-privilege permissions | Release provenance (trusted publishing) |
| T17 | **Compromised or misbehaving provider tool** | 1 | 3 | 3 | Providers are user-installed and run as subprocesses. Version and digests are recorded. Out-of-range versions give UNKNOWN | — |

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
| T20 | The agent deletes `.repomarshal/state/base.json` *and* moves refs before the first `done` | 1 | 3 | Falls back to automatic BASE. CI with an explicit BASE is the anchor |

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
