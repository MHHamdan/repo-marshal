# Prior-art notes: Preloop verification gate

Read-only inspection of a local clone. No code copied. Labels: **VERIFIED** (path:line at the SHA below), **INFERRED**, **UNKNOWN**.

| Field | Value |
|---|---|
| Identifier | github.com/preloop/preloop |
| SHA (HEAD) | `71dd091e8e6e7503a8105b3b18b772df9707e15c` (2026-10-13T03:13:37+02:00), 7,162 commits |
| Feature commit | `c762ff2a35eb7ea8971b1ed7a40235575ea49da2`, merge of PR #435 "Gate implementation publication on selected checks", 2026-09-06 |
| License | Apache-2.0 (`LICENSE`) |
| What it is | Self-hosted "AI agent control plane": an MCP firewall, a model gateway, YAML/CEL policies, human approvals, and agent "flows" run in containers |

## Mechanism (VERIFIED, `backend/preloop/utils/verification_selection.py` and `backend/preloop/models/schemas/verification.py` at c762ff2)

- **The profile.** A `VerificationProfile` (`version: "v1"`, `profile_id`) has three lists:
  - `always`: cheap checks required on every change;
  - `rules[]`: each rule has an `id`, a `description`, fnmatch `path_globs` and `commands[]`;
  - `unknown_default`: required for any changed path that no rule matches. It is never allowed to resolve to an empty list.
- **Each command** has `id`, `command` (a shell string), `reason`, `scope` (docs/migration/frontend/backend/api/shared/unknown) and `timeout_seconds`.
- **`select_from_raw(profile, changed_files)` is pure stdlib.**
  - Its output is the union of commands across matched rules, keyed by id. Each check records `selected_by: [rule ids | "always" | "unknown_default"]`.
  - If any changed file is uncovered, or the change list is empty, `unknown_default` is added.
- **`evaluate_from_raw` makes a fail-closed publication decision.** It denies publication when:
  - evidence is missing;
  - the producer is not `preloop-verifier` or the verifier version differs;
  - the profile id or version is stale;
  - `commit_sha` or `tree_hash` don't match;
  - the working tree is dirty;
  - a required check is missing, its command text changed, it was skipped, or it has no exit code;
  - any check exits non-zero.

  The resulting statuses are `passed`, `failed` and `blocked`.
- **Trust boundary.** The profile travels in the flow configuration, **not inside the repository under test**, "so the agent cannot narrow required checks or edit the profile inside its own PR" (docs/guide/flows/automated-issue-implementation.md, added in c762ff2).
- **Agent-reported verification is kept as a claim, not evidence.** Sandbox-produced records are labeled `authenticated: false`.

## Relevance to RepoMarshal (INFERRED)

- **Closest existing mechanism.** This is the nearest implementation of "diff → selected required checks → fail-closed gate" for coding agents. RepoMarshal must not claim path-based check selection as novel.
- **What Preloop does not do:**
  - The profile is hand-authored per repository. Nothing infers it from the repo's toolchain.
  - Selection uses path globs only, with no content signals: dependency additions, auth API use, test deletions and the like.
  - Commands are opaque shell strings with exit-code semantics. There is no UNKNOWN or WAIVED and no typed providers (security scanner, runtime verifier).
  - Skipped checks have no recorded reason; only selected checks carry `selected_by`.
  - There is no context or skill planning before work starts.
  - It is embedded in a server platform that runs agents in containers. It is not a local tool any agent can call.
- **Ideas to adopt independently** (concepts, not code):
  - `always` / `rules` / `unknown_default` with "unknown impact never resolves to empty";
  - evidence bound to commit, tree, producer and profile version;
  - a trusted policy that the change under evaluation cannot modify.
- **Interop opportunity.** A RepoMarshal Change Plan could be exported as a Preloop `VerificationProfile` for teams that run Preloop.
