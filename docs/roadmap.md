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

## SHOULD: next, in priority order

1. **Test-tamper signals:** `tests.deleted`, `tests.skip_added`, `tests.discovery_narrowed` (T2).
2. **Remaining catalogue signals:** `ci.changed`, `agent.config_changed`, `agent.skill_changed`,
   `dependency.changed`, `db.migration`.
3. **Repository CI:** lint, tests and the identity check with pinned actions. Plus a documented recipe
   for running `repomarshal done` in a user's CI as the authoritative anchor (T4, T5, T7).
4. **`init` and `doctor`.**
5. **Claude Code Stop-hook shim** (`done --hook claude-code`). Snippets for the other hosts are
   printed by `init --print`.
6. **MCP stdio server** (`repomarshal[mcp]`): `plan`, `done`, `explain`.
7. **SkillSpector adapter** for `skill_audit` (T9).
8. **Playwright adapter** for `runtime` (JSON reporter; `expected == 0` → UNKNOWN).
9. **Inventory from more sources:** `AGENTS.md ## Commands` and `Makefile` targets.
10. **Evidence hash chain** (`prev_digest`) and a `status` command that reports STALE.
11. **Waivers:** a recorded reason, governed by `completion.waivers_permit_pass`.
12. **README** with real demo output and the honest boundaries from the threat model.
13. **CI test for the startup budget.**

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
