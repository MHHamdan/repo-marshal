# Roadmap

_Phase 5 (MISSION.md §66)._

**v0.1 MUST** is exactly what the MISSION.md §42 demonstration needs end to end: one fixture repository,
three changes (A: docs, B: frontend, C: auth), and `plan` plus `done` producing visibly different,
explained plans and honest verdicts. Everything else is ranked below it. The SHOULD order follows the
threat model priorities.

## v0.1 MUST: the §42 demo, end to end

| # | Item | Needed for |
|---|---|---|
| M1 | `git.py`: BASE resolution (`--base`, then origin refs, then local main/master); changed files including staged, unstaged and untracked; per-file added and removed lines; reading files at BASE | A, B, C |
| M2 | Tree digest (temporary index + `add -A` + `write-tree`, excluding `.repomarshal/`) | `done` binding |
| M3 | Inventory at BASE from `package.json` scripts (with lockfile → pm), `pyproject.toml` (ruff, mypy, pytest, `tests/integration`), and `repomarshal.toml` `[gates.*]` (argv arrays). Scopes are the root plus immediate subdirectories. Availability via `PATH` | B, C |
| M4 | Policy loaded from BASE and validated. Invalid → BLOCKED | C (configured gates) |
| M5 | Signals: `docs.changed`, `code.changed`, `frontend.ui`, `tests.changed`, `security.auth_path`, `security.sensitive_identifier`, `build.config_changed`, `policy.self_modified`, `coverage.unclassified` | A, B, C, plus safety of M4 |
| M6 | Decision rules for docs, format, lint, typecheck, unit, integration, secrets, security_scan, runtime and a11y. Every decision carries a reason. Widening applies | A, B, C |
| M7 | Native gates `docs:links` and `secrets:diff` | A, C |
| M8 | Context selection P1–P4 with labelled size estimates and an excluded list | A, B, C |
| M9 | Skill recommendation from local `SKILL.md` frontmatter | B, C |
| M10 | `repomarshal plan [task] [--json] [--explain] [--base]`, in forecast and binding modes | A, B, C |
| M11 | `repomarshal done [--json] [--dry-run]`: run REQUIRED command gates (argv, cwd, timeout), compare the before and after digests, write an evidence record, return a verdict with exit codes 0/1/2/3/4 | A, B, C |
| M12 | JSON Schemas for the plan and evidence (`schema_version: "0.1"`), checked in tests | contract |
| M14 | **`gates.definition_changed`** (promoted from SHOULD #1 by the owner, 2026-10-13): if a gate's definition differs between BASE and the working tree (package.json script body, Python tool config, `[gates.*]` table), the gate's result is UNKNOWN whatever its exit code. Without this, changing the test script to `exit 0` yields PASS (threat T3) | core promise |
| M13 | The `webapp` fixture plus tests: the three scenario plans; a broken gate → FAIL; unavailable → UNKNOWN; no required gates → UNKNOWN; tree changed during the run → UNKNOWN; task text cannot remove gates; policy edits do not apply to the current plan | proof |

**v0.1 exit criteria:**
- The tests above pass.
- `plan` cold start is under 300 ms on the fixture (measured).
- The three demo plans are reproduced verbatim in the README from real output.

## Done in v0.2

- Test-tamper signals `tests.deleted`, `tests.skip_added` and `tests.discovery_narrowed`, which taint
  the affected gates so they report UNKNOWN (T2).
- Catalogue signals `ci.changed` and `agent.config_changed` (T4, T7).
- Repository CI: pytest on 3.11–3.13, ruff, the MCP extra, a dogfood `done` on PRs, and actions
  pinned by SHA. Plus the CI recipe in `docs/guides/ci.md`.
- `init`, `doctor`, and the fail-closed Claude Code Stop-hook shim (`done --hook claude-code`).
- The MCP stdio server as the optional extra `repomarshal[mcp]`.

## Done in v0.3

- **Waivers** from humans only: PR label plus reason comment or an approving CODEOWNER review in CI,
  `repomarshal waive` at a terminal locally; only for UNKNOWN/UNAVAILABLE, only when the BASE
  policy sets `completion.waivers_permit_pass`. The dogfood CI job installs its judge from BASE.
- **Stop-hook loop termination:** a repeated, identical non-PASS verdict is let through with an
  UNVERIFIED message to the user; a new or worse verdict always blocks.
- **SkillSpector** (`skill_audit`) and **Playwright** (`runtime`) providers, experimental, with
  pinned tested ranges and the tool version recorded in evidence.
- Signals `agent.skill_changed` and `dependency.changed`.
- Inventory from `Makefile` targets, workspace packages, `packageManager`, `test:*` scripts and
  non-mutating check scripts.
- A field evaluation on 27 commits from 5 real repositories (`docs/evaluation.md`).

## SHOULD: next, in priority order

1. **Evidence hash chain** (`prev_digest`) and a `status` command that reports STALE.
2. **Workspace-aware gate deduplication:** a root `test` that already runs every package should not
   also require each package's unit gate (field evaluation §9).
3. **`db.migration` signal.**
4. **Inventory from `AGENTS.md ## Commands`.**
5. **CI test for the startup budget.**
6. **Publish to PyPI**, with trusted publishing.

## MAY: after the core is proven

- **Result cache:** HMAC'd, scoped subtree keys; never for security or runtime gates. Plus promotion
  of fast expensive gates from measured durations.
- **Further provider adapters:** Reticle, when `.reticle/` exists; AgentProof as a test-integrity gate;
  `pip-audit` and `npm audit` for `dep_audit`; `bandit` and `semgrep` for `security_scan`.
- **Exports:** SARIF, a DoneProof receipt and a Preloop verification profile.
- **Context and skill providers:** a Chisel spec reader (read-only), the UI Skills remote catalog
  (network opt-in), UI/UX Pro Max advisory queries.
- **Measurement:** real tokenizer counts when a tokenizer is installed.
- **More inventory:** CI `run:` hints, Go and Rust toolchains, deeper monorepo scopes.
- **Distribution:** an Agent Skills `SKILL.md` and an Agent Plugins package.

## NOT NOW

- LLM-based classification of any kind.
- Selecting individual tests (delegate to Jest, Playwright, Nx and pytest plugins).
- Tool-call enforcement, sandboxing, network isolation.
- Writing host agent config files.
- Signing evidence, or key infrastructure.
- Telemetry, a hosted service, a dashboard.

## Metrics to report at v0.1

These are measured, never estimated:
- fixture planner precision: required gates selected and expensive gates avoided, per scenario;
- verdict honesty: the counts of FAIL and UNKNOWN cases covered;
- context size, selected versus candidate (labelled as an estimate);
- `plan` cold and warm latency.
