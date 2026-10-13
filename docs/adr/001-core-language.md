# ADR-001: Core language and runtime dependencies

Status: accepted (2026-10-13)

## Context

`repomarshal done` runs on every agent stop hook, and `plan` runs at the start of each task, so
process startup sits on the agent's critical path. Our target is **under 300 ms cold start for `plan`**,
excluding the gates it runs. The project is security-adjacent, so every runtime dependency must be
justified (MISSION.md §39). We also want contributors from the ecosystems we integrate with:
SkillSpector and AgentProof are Python, Playwright and Reticle are TypeScript, and the MCP SDKs
include Python and TypeScript.

## Measurement

We built prototypes of `plan` that each do the same work: parse arguments, load a small config,
run `git rev-parse HEAD` plus `git diff --name-only HEAD~1`, and print JSON. Each was timed as a
fresh process, 40 runs after 3 warm-ups, with a warm OS file cache. The machine was a 4-vCPU Xeon
at 2.1 GHz (cloud container) on 2026-10-13. The harness and prototypes were throwaway scripts and
are not in the repo.

| Variant | median ms | p90 ms |
|---|---|---|
| The 2 git calls alone (floor) | 6 | 7 |
| Go 1.24 static binary | 7 | 7 |
| Node 22 script | 58 | 71 |
| Python 3.11, stdlib only (argparse, dataclasses, json, tomllib) | 56 | 69 |
| Python 3.13, stdlib only | 61 | 69 |
| Python 3.11, stdlib, installed console script | 55 | 60 |
| Python 3.13, stdlib, installed console script | 63 | 76 |
| Python 3.13, stdlib, launched via `uvx --offline` (cached env) | 109 | 130 |
| Python 3.11 + PyYAML 6.0.3 (C loader) | 62 | 75 |
| Python 3.13 + PyYAML 6.0.3 (C loader) | 73 | 82 |
| Python 3.11 + typer 0.27 + pydantic 2.14 + PyYAML | 186 | 211 |
| Python 3.13 + typer 0.27 + pydantic 2.14 + PyYAML | 196 | 223 |
| Python 3.13 bare interpreter (`-c pass`) | 19 | 24 |

What the numbers show:
- Stdlib Python uses about 20% of the 300 ms budget.
- typer + pydantic adds about 130 ms. That is two thirds of the budget before any real work.
- Launching through `uvx` adds about 50 ms.
- Go is about 8× faster in absolute terms, but its 50 ms advantage is invisible next to the gates
  `done` runs, which take seconds or more.

## Decision

- **Language:** Python ≥ 3.11.
- **Distribution:** `uv tool install repomarshal` or `pipx install repomarshal`. `uvx repomarshal`
  is fine for one-off use. Hooks should call the installed console script, not `uvx`.
- **No third-party runtime dependencies in the core:**
  - CLI: `argparse`.
  - Data model: `dataclasses`, serialised with explicit `to_dict()`.
  - Process execution: `subprocess` with argv lists.
  - Hashing: `hashlib`.
- **Configuration format: TOML** (`repomarshal.toml`), read with stdlib `tomllib`. This replaces the
  YAML that MISSION.md §27 suggested: YAML would add the only runtime dependency, and TOML is
  already the Python packaging convention.
- **Validation:** config and IR validation are hand-written and return structured errors. JSON
  Schemas for the plan and evidence are published as contracts and checked in tests with
  `jsonschema`, which is a dev dependency only.
- **Optional extras with lazy imports:** `repomarshal[mcp]` (the official MCP SDK) is never
  imported on the `plan`/`done` path.
- **CI guard:** a test asserts that `plan` imports no non-stdlib module.

## Alternatives

- **Go.** Fastest, and ships as a single binary. Rejected for v0.1:
  - The contributor pool is smaller in the agent-security tooling around us.
  - We need no concurrency or systems features.
  - The measured gain doesn't matter at our budget.
  - **Revisit if** startup becomes a measured problem on large repos.
- **TypeScript/Node.** Same startup cost as Python, and a strong MCP and Playwright ecosystem.
  Rejected because it needs Node on Python-only repos, and its supply-chain surface (npm transitive
  dependencies) is larger for a security-adjacent tool.
- **Python with typer + pydantic.** Nicer ergonomics, but 3× the startup cost and two runtime
  dependencies that change often.
- **Rust.** Same startup benefit as Go, with the highest contributor barrier. Rejected.

## Consequences

- Validation code is written by hand. Keep the IR small and test it.
- Python ≥ 3.11 is required, for `tomllib` and modern typing.
- The startup budget becomes a regression test: `plan` must stay under 300 ms on the fixtures in CI,
  measured, not assumed.
- Contributors need only `uv` or `pip`; there is no build step.
