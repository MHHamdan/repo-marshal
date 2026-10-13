# ADR-004: Evidence model

Status: accepted (2026-10-13)

## Context

"An agent's claim of completion is not evidence" (MISSION.md §24). Agents usually claim done
*before* committing. The agent can write any file RepoMarshal writes, because it runs as the same
OS user. The prior art points two ways:
- Preloop and AgentProof bind evidence to commit and tree.
- Reticle's OVP never lets unknown collapse into pass.

## Decision

- **Re-execution is the only source of truth.** `done` derives its verdict solely from gates it
  executes in that invocation. Files under `.repomarshal/` are outputs (an audit trail), never inputs.
  There is **no result cache in v0.1**.
- **Binding.** Each evidence record carries `base`, `head`, `plan_hash`, `policy_digest`,
  `tree_digest_before` and `tree_digest_after`.
  - The tree digest is the git tree id of a temporary index built with `git add -A`. It covers
    staged, unstaged and untracked non-ignored files, and excludes `.repomarshal/`
    (architecture §5.1).
  - If before ≠ after, the verdict is UNKNOWN.
  - A later tree mismatch makes the evidence STALE.
- **Records.** `.repomarshal/evidence/<ts>-<plan>.json` holds:
  - per-gate argv, exit code, duration, sha256 of stdout and stderr, and a redacted 40-line tail;
  - the RepoMarshal version and `record_digest`.

  A hash chain (`prev_digest`) is SHOULD. Signing is NOT NOW.
- **Anchor.** A local verdict is advisory for the agent's inner loop. The authoritative verdict comes
  from re-running `done` where the agent has no write access (CI or a reviewer). Every `done` output
  says so.
- **Cache (later).**
  - Key: `sha256(argv, cwd, scope subtree id, root config digest, tool version, env fingerprint)`,
    with entries HMAC'd using a key kept outside the repository.
  - Security and runtime gates are never cached.
  - It must not ship before tests show it invalidates on every input in that key.

## Alternatives

- **Trust evidence files the agent writes (receipt validation, DoneProof-style).** Rejected: these
  are claims, not evidence.
- **Bind to HEAD only.** Rejected: uncommitted work would be unverifiable, or verified against the
  wrong content.
- **Sign evidence locally.** Rejected for v0.1: a same-user agent can read the key. It would be
  security theatre without an external key holder.

## Consequences

- Every `done` re-runs its required gates. Change-aware selection keeps that cost bounded, and a
  correct cache can reduce it later.
- RepoMarshal can truthfully say what it proves: these commands, run by RepoMarshal against exactly
  this content, exited this way. It must not claim more.
