# ADR-005: Change planner

Status: accepted (2026-10-13)

## Context

The planner is the product (gap analysis, Q4). Preloop already selects checks from path globs over a
hand-written profile. AgentSmith infers a repo-wide gate inventory, but not per change. UI Skills'
routing is prose for the LLM. The planner must be deterministic and explainable, safe against a
change that tries to shrink its own plan, and useful without configuration.

## Decision

- **Compiler pipeline:** ChangeSet → signals → gate decisions, plus context and skills → Plan IR
  (`schema_version: "0.1"`, `plan_hash`). The algorithm is in architecture §4.2.
- **Signals are data.** A versioned built-in catalogue has stable ids (`security.auth_path`,
  `tests.deleted`, …) and matches on path segments, globs, extensions, file status and *added or
  removed lines*. Policy may add rules. It may not remove or override built-in ones.
- **Every decision has a reason** citing signal ids and scope, for REQUIRED, SKIPPED, NOT_APPLICABLE
  and UNAVAILABLE alike. A skip without a reason is a bug.
- **Safety floor on minimisation:**
  - Cheap and standard gates are required whenever their scope has code changes.
  - Expensive gates need a triggering signal.
  - Widening signals (`build.config_changed`, `ci.changed`, `coverage.unclassified`,
    `agent.config_changed`, `policy.self_modified`, `tests.deleted`) apply the plan to all scopes.
  - A file no rule recognises can never shrink the plan.
- **Untrusted inputs can only add.**
  - Task text maps keywords to `task.*` signals that add requirements or forecast domains. Nothing
    in task text can skip a gate.
  - Policy and gate definitions are read from BASE.
- **Classification is deterministic** (MISSION.md §11). No LLM in v0.1. Optional model-assisted
  classification later must be opt-in, labelled, and add-only.
- **Two modes:**
  - `forecast`: the task only, no diff yet. Advisory.
  - `binding`: compiled from the actual diff at `done`.

## Alternatives

- **A hand-written profile only, like Preloop.** Rejected as the *only* path: there is no value
  before configuration. Kept as an override via `[gates.*]` and `[[signals]]`.
- **Path globs only.** Rejected: they miss content signals (secret material, removed test functions,
  dependency-section edits).
- **LLM classification.** Rejected for v0.1: it isn't reproducible, and it is open to prompt
  injection from task text and repository content.
- **Always run everything (AgentSmith rule R5).** Rejected for the inner loop because of cost. It
  remains CI's job.

## Consequences

- Catalogue quality is the moat and the main risk. Every rule needs a fixture test, and noisy rules
  (for example `security.sensitive_identifier`) are kept at `standard` risk so they add the secrets
  gate without escalating the whole plan.
- Plans are reproducible: the same inputs give the same `plan_hash`. That makes "why was X skipped?"
  answerable and testable.
