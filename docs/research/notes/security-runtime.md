# Prior-art notes: security scanning and runtime verification

Scope: NVIDIA/SkillSpector, reticlehq/reticle, microsoft/playwright, studied as candidate
SecurityChecker and RuntimeVerifier backends for RepoMarshal. All reads were done on local clones
under `<research-clones>/`. No upstream repo was modified and no code was copied.
GitHub web/API was not reachable, so issues, PRs, release pages and discussions were NOT inspected.

Labels: **VERIFIED** means seen in a file (path:line) or observed by running the tool locally.
**INFERRED** means reasoned from evidence. **UNKNOWN** means not determinable here.
Paths are relative to each repo root.

---

## 1. NVIDIA/SkillSpector

| Field | Value |
|---|---|
| Identifier | github.com/NVIDIA/skillspector (clone dir `NVIDIA_SkillSpector`) |
| SHA | `69d7d059da38160296b2311320ed4c72a9c3fdb6` |
| Last commit | 2026-10-12 21:40:43 +0530 ("Merge pull request #816 … fix-archive-component-paths") |
| Latest tags | v2.12.0 (2026-09-23), v2.11.2 (2026-09-10), v2.11.1, v2.11.0, v2.10.0 |
| Package version | 2.12.0 (`pyproject.toml`). CHANGELOG.md:1 still says "2.12.0 (Unreleased)" even though the tag exists |
| History | 1176 commits. Tests: 161 `test_*.py` files under `tests/` |

### License (VERIFIED)
- `LICENSE` is Apache-2.0. It is the only LICENSE file in the repo.
- `pyproject.toml` sets `license = "Apache-2.0"` and the classifier "OSI Approved :: Apache Software License".
- Source headers carry `SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES` and `SPDX-License-Identifier: Apache-2.0` (src/skillspector/cli.py:1-2).
- `THIRD_PARTY_NOTICES.md` lists the runtime dependency licenses (typer MIT, rich MIT, httpx BSD-3, PyYAML MIT, pydantic MIT, openai, …).
- There is no NOTICE file.
- Contributions require DCO sign-off (`Signed-off-by`), not a CLA (CONTRIBUTING.md:22-35).
- `package.json` is a private Pi-extension shim (`skillspector-pi` 2.2.3) and has no license field.
- The YARA rules ship in `src/skillspector/yara_rules/`, including `malware.yar.b64`. Their provenance is not separately licensed in the repo. **UNKNOWN** whether any of them come from third-party rule sets; check THIRD_PARTY_NOTICES before redistributing them.

### What it is (VERIFIED, README.md:1-31, 909-958)
SkillSpector is a security scanner for agent skills. It runs a LangGraph pipeline: a static stage
(regex patterns, Python AST, taint tracking, YARA, MCP least-privilege and tool-poisoning checks,
and OSV.dev lookups for supply chain) and an optional LLM semantic stage. The README claims 71
patterns in 17 categories.

Inputs are a Git URL, a file URL, a `.zip`, a `.md` file or a directory (cli.py:452-456).
`--recursive` scans subdirectories that contain a SKILL.md. `--mcp-registry` scans MCP Registry payloads.

What it scans, from my own runs:
- **VERIFIED by probe:** a directory without a SKILL.md is still accepted. It scanned `a.py` and `.mcp.json` as components, returned score 0/SAFE and exited 0.
- **VERIFIED by probe:** a `.mcp.json` containing `{"command":"npx","args":["-y","evil"]}` produced no finding.
- **INFERRED:** it does not specifically parse agent MCP client configs. Its MCP checks target MCP server code and tool metadata that are bundled inside a skill (LP1-4, TP1-4) and registry payloads.
- The `--exclude` flag requires a SKILL.md (cli.py:476-483).

The tool never executes the scanned skill (README.md:955).

### CLI (VERIFIED, src/skillspector/cli.py)
- Commands: `scan`, `mcp`, `baseline` (from `skillspector --help`). Global flags: `--version/-v`.
- `scan INPUT` flags (cli.py:450-610):
  - output: `-f/--format terminal|json|markdown|sarif`, `-o/--output`
  - scope: `--exclude` (repeatable), `--recursive/-r`
  - analysis: `--no-llm`, `--yara-rules-dir`
  - baseline: `-b/--baseline`, `--show-suppressed`, `--use-shipped-baseline`
  - transitive: `--transitive`, `--transitive-depth`, `--transitive-allow-prefix`, `--transitive-deny-prefix`
  - gates: `--fail-on-incomplete`, `--min-coverage PCT`, `--fail-on-findings`
  - other: `-V/--verbose`, `--mcp-registry`, `--mcp-registry-compare`
- `mcp --transport stdio|http --host --port` (cli.py:3360-3403).
- `baseline PATH -o --no-llm --reason -V` (cli.py:3406-3438).
- Exit codes (cli.py:860-881; README.md:783-793; `RISK_THRESHOLD = 50` at constants.py:35):
  - **0**: score ≤ 50 and no strict gate fired.
  - **1**: score > 50, or `--fail-on-findings` / `--fail-on-incomplete` / `--min-coverage` tripped.
  - **2**: error, or `execution_successful == false`.
  - SAFE and CAUTION both exit 0 by default.
- Run locally (VERIFIED): `--no-llm` on the probe skill gave exit 1, score 78, HIGH, DO_NOT_INSTALL, with findings P1, PE3, E2, LP3, E1. The `.mcp.json` dir scan took about 3.2 s wall-clock.

### JSON output contract (VERIFIED, src/skillspector/nodes/report.py:1410-1473; observed in a run)
- Top-level keys, in order: `skill{name,source,scanned_at}`, `risk_assessment{score,severity,recommendation,max_issue_severity}`, `components[]`, `structured_summaries[]`, `issues[]`, `suppressed_count`, `suppressed[]`, `metadata{…}`, `execution_successful`, `analysis_completeness{…}`.
- `severity` takes `LOW|MEDIUM|HIGH|CRITICAL`. `recommendation` takes `SAFE|CAUTION|DO_NOT_INSTALL` (README.md:835-836). `max_issue_severity` adds `NONE` (report.py:499-516).
- **The report has no top-level schema-version field.** The only version is `metadata.skillspector_version`. A nested `metadata.llm_provenance.schema_version = 1` exists (llm_provenance.py:25).
- The README says the exit code plus the JSON are "a stable contract". Consumers should rely only on the documented fields and treat the rest as best-effort (README.md:781, 860).
- Issue fields come from `Finding.to_dict` (models.py:240-276): `id` (rule id), `finding_id`, `category`, `pattern`, `severity`, `confidence`, `location{file,start_line,end_line,start_column?,end_column?}`, `finding`, `explanation`, `remediation`, `code_snippet`, `intent`, `tags[]` (e.g. `llm-unconfirmed`), `evidence{}`, `match_fingerprint`, `occurrences`, plus optional `source_*` and `transitive_depth`.
- `metadata` fields: `has_executable_scripts`, `skillspector_version`, `llm_requested`, `llm_available`, `meta_analysis_applied`, `inference_usage[]`, `llm_provenance{}`, `filtering_mode` (set to "heuristic" when meta-analysis did not run), and optionally `llm_error`, `llm_degraded`, `llm_calls_*`, `transitive_*` (report.py:1245-1370).
- `analysis_completeness` (observed): `total_components`, `scanned_components`, `coverage_percent`, `is_complete`, `status`, `execution_successful`, `fully/partially/entirely_(un)inspected_files`, `ledger_exceptions[]`, `scope_exclusions[]`, `analyzer_statuses[]`.
- Scoring (report.py:545-575; README.md:657-674):
  - base points CRITICAL 50, HIGH 25, MEDIUM 10, LOW 5;
  - each finding is scaled by its confidence;
  - per-rule diminishing weights of 1, 0.5 and 0.25, with occurrences after the third ignored;
  - ×1.3 only for findings in executable script files;
  - score floors of 51 for SC8 and some SC9/BH2/BH3 cases (report.py:520-543);
  - bands: 0-20 LOW/SAFE, 21-50 MEDIUM/CAUTION, 51-80 HIGH, 81-100 CRITICAL, both of the last two mapping to DO_NOT_INSTALL.
- Note that `risk_assessment.severity` is confidence-weighted, so it can read LOW while `issues[]` contains a HIGH finding (report.py:500-506). Gate on `max_issue_severity` when the worst finding matters.

### SARIF (VERIFIED, report.py:426-455, 652-1000; sarif_models.py:24, 182; observed)
- The document has `version: "2.1.0"` and `$schema` set to schemastore `sarif-2.1.0-rtm.4.json`. It has one run with `tool.driver{name:"skillspector",version,rules}`, `results`, `columnKind:"unicodeCodePoints"` and `invocations[{executionSuccessful,toolExecutionNotifications,properties.analysisCompleteness}]`.
- `level` mapping: CRITICAL and HIGH map to `error`, MEDIUM to `warning`, LOW to `note`.
- `properties.security-severity` mapping: 9.5, 8.0, 5.5, 3.0.
- Each result's `properties` carries `findingId`, `severity`, `category`, `pattern`, `confidence`, `finding`, `explanation`, `remediation`, and so on.
- Output is validated against the schema before it is emitted (`validate_sarif_report`).

### MCP server (VERIFIED, src/skillspector/mcp_server.py:252-322; README.md:435-491)
- Requires the extra `skillspector[mcp]` (`mcp>=1.29,<2`) and uses FastMCP.
- **One tool:** `scan_skill(target: str, use_llm: bool=True, output_format: str="json")`.
- It returns `target`, `risk_score`, `severity`, `recommendation`, `safe_to_install`, `execution_successful`, `analysis_completeness`, `findings`, `report`, `llm_requested`, `llm_available`, `llm_used`, `scan_mode` ("static+llm" or "static-only"), and `version` (mcp_server.py:225-240).
- `safe_to_install` is stricter than the CLI's exit 0. It is true only when all of the following hold (mcp_server.py:214-221): score ≤ 50, execution succeeded, nothing is uninspected or partially inspected, completeness is complete, and the LLM requirement is met.
- The HTTP transport is loopback-only and unauthenticated, and it rejects local paths. The README notes a known stdio initialize hang (issue #199, not inspectable here).

### Offline / no-LLM (VERIFIED by run)
- I ran `scan --no-llm` with no API keys and HTTPS_PROXY pointed at a dead port. It succeeded with JSON and SARIF output.
- The default `use_llm` is true. Without `--no-llm` and without credentials, the scan falls back to static analysis and sets `metadata.llm_error` (report.py:1336-1360). **INFERRED:** it still exits normally.
- SC4 (supply-chain CVE check) calls api.osv.dev even with `--no-llm` and falls back to a bundled list when offline (README.md:940-957). I found no flag that disables SC4; only `SKILLSPECTOR_OSV_TIMEOUT` exists.
- **Implication:** for strict local-first behaviour, RepoMarshal should run SkillSpector in a network-denied sandbox or accept that it sends dependency names to OSV.

### Python API (VERIFIED, src/skillspector/__init__.py; README.md:977-996)
- Exports `graph`, `create_graph` and `__version__`. Usage is `graph.invoke({"input_path", "output_format", "use_llm"})`, which returns a state dict (`risk_score`, `risk_severity`, `filtered_findings`, …).
- The Development Status classifier is "3 - Alpha".
- The returned state is a LangGraph internal state, not a versioned API. The caller must also clean up temp dirs (docs/DEVELOPMENT.md:184).
- **INFERRED:** the API is not stable enough to embed. The CLI JSON is the documented contract.

### Dependency footprint (VERIFIED)
- Requires Python `>=3.12,<3.15`.
- Runtime dependencies (pyproject.toml): typer<0.24, rich, httpx, pywhatwgurl==0.1.1, regex==2026.5.9, packaging, pyyaml, pydantic, openai, langgraph, langchain-anthropic/-aws/-core/-openai, boto3, langsmith, yara-python, google-auth.
- All LLM SDKs are installed even when only `--no-llm` is used.
- A fresh `uv pip install` produced a **244 MB venv with 149 site-packages entries** (measured).
- A Docker image (`python:3.12-slim-bookworm`) is buildable locally from the repo Dockerfile; there is no published image reference in the repo.

### Integration recommendation
**Subprocess adapter: sufficient and preferred.** Invoke:

```
skillspector scan <path> --no-llm --format json [--fail-on-incomplete]
```

Then:
- Parse stdout or `-o file`.
- Map exit code 2 to BLOCKED (tool error), and `analysis_completeness.is_complete == false` to UNKNOWN.
- Choose PASS or FAIL from `recommendation`, `max_issue_severity` and RepoMarshal's own policy.
- Record `metadata.skillspector_version`, `scan_mode` / `llm_requested` and the SARIF output as evidence.
- Treat it as an optional external tool installed via `uv tool install` or Docker. Do not make it a pip dependency: the footprint is too large, Python is pinned to ≥3.12, and the stack is LangChain-heavy.
- The MCP route is unnecessary for a planner. It is only useful if agents call it directly.

### Attribution obligations
- Under Apache-2.0, merely invoking the tool as a subprocess creates no obligation (INFERRED).
- If RepoMarshal redistributes it, in Docker or vendored, it must include the LICENSE and keep the SPDX headers. There is no NOTICE file to carry.
- Naming it in docs: credit "NVIDIA SkillSpector (Apache-2.0)" and avoid implying NVIDIA endorsement (Apache §6 trademarks).

### Compatibility concerns
- No JSON schema version field. Pin a tested version range and validate against the field set listed above.
- Python ≥3.12 only.
- LLM is enabled by default. RepoMarshal must always pass `--no-llm` unless the user opts in, because file contents go to the provider.
- OSV egress happens even with `--no-llm`.
- CAUTION collapses into exit 0.
- Fast-moving: tagged releases roughly every 2 weeks, and the alpha classifier.

### Strengths
- Explicit exit-code contract.
- SARIF 2.1.0 output with schema validation.
- Completeness ledger (`analysis_completeness`) that maps cleanly onto UNKNOWN.
- Baselines/suppression.
- Honest static-vs-LLM reporting.
- Never executes scanned code.
- Apache-2.0 with DCO.

### Weaknesses
- Skill-centric: it does not flag risky MCP client config entries (probe).
- Heavy dependencies.
- No schema version.
- Python API is not stable.
- OSV network call cannot be disabled.
- Regex heuristics produce false positives (README.md:917-918 says "moderate precision").

---

## 2. reticlehq/reticle

| Field | Value |
|---|---|
| Identifier | github.com/reticlehq/reticle (clone dir `reticlehq_reticle`) |
| SHA | `1a337fa254466c33fb5888ddc0e269e1811eb2f9` |
| Last commit | 2026-10-13 04:35:58 +0000 ("fix(server): preserve replay timeout budgets (#1571)") |
| Latest tags | v3.6.0 (2026-10-07), v3.5.0, v3.4.0, v3.3.0, v3.2.0 |
| Package version | Monorepo 3.7.0, not yet tagged. `open-verification` is 1.0.0 |
| History | 2730 commits. 1614 `*.test.*` files. pnpm + turbo monorepo; root engines `node >=22.12` for development |

### What it actually is (VERIFIED, README.md:10-76; docs/architecture.md:7-30)
Reticle is a runtime verification layer for web and desktop apps built by coding agents. It has
three parts:
1. A **dev-only in-page SDK** (`@reticlehq/react` or `@reticlehq/browser`, wired by the Vite, Next or Babel plugins) that instruments DOM, network, console, framework stores and React fibers.
2. A **local Node server/daemon** (`@reticlehq/server`, bin `reticle`) that runs a WebSocket bridge on `127.0.0.1:4400` and an **MCP server** for agents.
3. A git-checked **`.reticle/` workspace** that holds flows, the contract and baselines.

Agents look, act, observe and assert through MCP tools, and each check returns a verdict with
evidence and the source `file:line`. Saved flows replay deterministically with no LLM.

There is also a hosted platform (app.reticle.sh): "Harness" auto-drive (paid credits), a dashboard,
and sync. It is optional and needs an account.

**Key constraint (INFERRED, strongly supported):** Reticle verifies only apps that embed its SDK. It
must modify the target app's dev build: `reticle init` adds dev dependencies, edits the build
config, and appends to CLAUDE.md/AGENTS.md (README.md:91-101, 226-233). It cannot verify an
un-instrumented app or a non-JS stack beyond its realm adapters (browser, electron, cli-realm).

### Licenses per component (VERIFIED: every LICENSE file plus each package.json `license`)

| Path | Package | License |
|---|---|---|
| `open-verification/` | `open-verification` 1.0.0 | Apache-2.0 (LICENSE, package.json) |
| `core/` | `@reticlehq/core` | Apache-2.0 (+ NOTICE "Copyright 2026 Reticle Labs") |
| `engine/` | `@reticlehq/engine` | Apache-2.0 (+ NOTICE) |
| `adapters/realm/browser/` | `@reticlehq/browser` | Apache-2.0 (+ NOTICE) |
| `adapters/realm/electron/` | `@reticlehq/electron` | Apache-2.0 (+ NOTICE) |
| `adapters/realm/cli/` | `@reticlehq/cli-realm` (private, bin `reticle-cli-drive`) | Apache-2.0 (+ NOTICE) |
| `adapters/framework/react/` | `@reticlehq/react` | Apache-2.0 (+ NOTICE) |
| `adapters/lint/eslint/` | `@reticlehq/eslint-plugin` | Apache-2.0 (+ NOTICE) |
| `adapters/build/vite/` | `@reticlehq/vite-plugin` | Apache-2.0 (+ NOTICE) |
| `adapters/build/babel-plugin/` | `@reticlehq/babel-plugin` | Apache-2.0 (+ NOTICE) |
| `adapters/build/next/` | `@reticlehq/next` | Apache-2.0 (+ NOTICE) |
| `server/` | `@reticlehq/server` (bin `reticle`) | **FSL-1.1-ALv2** (server/LICENSE) |
| `init/` | `@reticlehq/init` | **FSL-1.1-ALv2** |
| `spec-runner/` | `@reticlehq/test` | **FSL-1.1-ALv2** |
| `server/src/features/ee/` | (inside server) | **Reticle Enterprise License**, source-available. Production use needs a subscription key (`RETICLE_LICENSE_KEY`). `server/LICENSE-ENTERPRISE` is identical |
| `apps/*`, `conformance`, root | private, no license field | Covered by the root `LICENSE`, which is a per-package overview and not itself a license grant (INFERRED) |

Notes on the licensing:
- The root `LICENSE` (lines 1-41) states this split and says each package's own LICENSE is authoritative.
- FSL forbids "Competing Use", meaning a commercial product or service that substitutes for the Software or for other Reticle offerings (server/LICENSE "Permitted Purpose"). Each version converts to Apache-2.0 two years after its release.
- The `ee/` README lists "policy enforcement / verify-before-merge gates" as planned Enterprise features (server/src/features/ee/README.md). **INFERRED risk:** gate-like features could move behind the key in future.
- Contributions require DCO `Signed-off-by` (CONTRIBUTING.md:191). No CLA was found.
- Telemetry is **on by default**: anonymous counters to Reticle. It can be turned off with `RETICLE_TELEMETRY=0`, `DO_NOT_TRACK=1` or `reticle telemetry disable` (docs/telemetry.md:169; README.md:103).

### PASS/FAIL/UNKNOWN semantics (VERIFIED)
- **Per-claim verdict.** Defined by `Verified` (core/src/verdict/verified-constants.ts:15-36) and by the OVP spec (open-verification/SPEC.md:267-278):
  - `yes`: proved, only at consequence grade over a cleanly closed window;
  - `no`: disproved or contradicted;
  - `unknown`: could not tell;
  - `no-fault`: everything was watched, nothing went wrong, and nothing was declared to prove. This is never a pass.
  - Rule [OVP-VERDICT-1]: "`unknown` MUST NOT collapse into either neighbour".
- **Run summary.** `RunOutcome` (open-verification/src/vocabulary/run.ts:127-165) has `pass`, `fail`, `partial` and `unknown`, computed by `outcomeOf()`. A violated blocking constraint means fail. Mixed proved and disproved means partial. Nothing proved means unknown. The summary MUST be recomputable from the verdicts (SPEC.md:280-293, OVP-VERDICT-2..4).
- **Reasons.** `VerifiedReason` gives a fine-grained reason enum (verified-constants.ts:53+), e.g. `version_skew`, `capability-absent`, `inconclusive`, `observation_lost`, `assertion_failed`, `contradicted`, `already_true`, `unclean_capture`, `vacuous_grade`, `nothing_declared`, `unsettled`, `proved`, `blind_spot`, ….
- **Adjudication order.** OVP §7.1 is a normative 11-clause order (SPEC.md:295-307). Its "grounds" are `nothing-declared`, `channel-not-observed`, `contradicted`, `assertion-failed`, `window-not-closed`, `coverage-impeached`, `suspicion-unresolved`, `declared-after-action`, `no-independent-consequence`, `already-true`, `proved`. The principle is that "I could not see" is always evaluated before "it did not happen".
- **Versions** (open-verification/VERSIONING.md:9-16):
  - protocol `OVP_VERSION = '1.0'` (run.ts:11);
  - flow grammar `OVP_FLOW_GRAMMAR_VERSION = 2` (memory.ts:45);
  - wire protocol 1;
  - package 1.0.0.
- `open-verification` publishes generated JSON Schemas (`./schema/*.json`) and test vectors (`./vectors/*.json`) (package.json exports).
- **Relevance to RepoMarshal:** OVP is the closest prior art to RepoMarshal's PASS/FAIL/UNKNOWN/BLOCKED evaluation. It is Apache-2.0, so its vocabulary and rules can be studied and reimplemented. RepoMarshal does not need it as a dependency.

### Saved flows format (VERIFIED, docs/flows.md:7-80; core/src/artifacts/flow-types.ts:642-647; flow-constants.ts:86-90)
- One JSON file per flow under `.reticle/flows/<name>.json`, committed to git. `.reticle/contract.json` holds the testable surface (testids, signals, stores).
- Shape: `{version: 2, name, createdAt, steps:[{tool:"reticle_act"|…, anchor:{kind:"testid"|…, value}, action, expect?:{kind:"signal",…}}], dynamic:[…], …}`.
- Readable versions are 1, `FLOW_FILE_VERSION` and `COMPARE_FLOW_FILE_VERSION`. Zod schemas live in `@reticlehq/core` (Apache-2.0).
- The rest of `.reticle/` is gitignored because session journals contain request bodies.

### CLI (VERIFIED, server/src/command/cli/cli-parse.ts:60-146; README.md:514-543; docs/cli/*.mdx)
- Local commands: `init`, `serve`, `stop`, `kill`, `restart`, `status`, `open`, `drive`, `verify`, `affected`, `gate`, `report`, `context`, `update`, `rollback`, `mcp`, `license`, `version`, `telemetry`, `feedback`, `doctor`, `setup`, `help`.
- Cloud commands: `connect`, `login`, `logout`, `whoami`, `link`, `sync`, `push`, `runs` (`runs regression`), `issues`, `project`, `memory`, `share`, `config`, `try`.
- **`reticle verify <url>`** (docs/cli/verify.mdx:7-75):
  - Boots the engine in drive mode, waits for the in-page SDK to dial back, replays all saved flows, prints a report and exits.
  - Flags: `--headed`, `--timeout N`, `--storage-state`, `--results-json <file>`, `--port`, `--session-id`, `--select <label>`, `--expect '<json predicate>'` / `--expect-file`, `--explore`/`--persona` (platform Harness), `--confirm-dangerous`.
  - Exit **0** only when the verdict is a pass. Exit **1** on fail, no session, no flows, or engine failure.
  - `--results-json` writes `{verdict, results:[{journey, verdict, reason?}]}` with verdicts `yes|no|unknown`. Skipped maps to unknown, and the run is `yes` only when every journey is (server/src/command/cli/verify-results.ts:17-40).
- **`reticle gate [--since <ref>] [--accept-coverage] [--hook] [file...]`** (docs/cli/gate.mdx:10-61):
  - Prints one JSON line `{t, event:"reticle_gate", pass, uncovered[], quarantined[], coverage{pct,covered,total}, …}`.
  - Exit codes: **0** covered, **1** something is uncovered, downgraded, deleted, unflowed, unexecuted or coverage regressed (also on any fault, which fails closed), **2** no saved flows, **3** the dashboard knows a regression (linked projects only).
- **`reticle affected [--since <ref>] [file...]`** prints one JSON line mapping changed files to flows. It always exits 0 (docs/cli/affected.mdx:10-46). This is change-aware selection and directly comparable to RepoMarshal's planner.

### MCP tools (VERIFIED, core/src/wire/tool-names.ts; server/src/surface/tools/tool-surface.ts:133-275)
- Launched with `npx @reticlehq/server mcp` over stdio. An HTTP transport is documented in docs/http-transport.md.
- Default (core) tier: `reticle_sessions`, `reticle_navigate`, `reticle_snapshot`, `reticle_query`, `reticle_act`, `reticle_act_and_wait`, `reticle_act_sequence`, `reticle_observe`, `reticle_network`, `reticle_console`, `reticle_wait_for`, `reticle_assert`, `reticle_state`, `reticle_inspect`, `reticle_feedback`, `reticle_session`, `reticle_verify`.
- Extended tier: `reticle_capabilities`, `reticle_flow_save`, `reticle_flow_replay`, `reticle_intent`, `reticle_context`, `reticle_record`, `reticle_screenshot`, `reticle_visual_diff`, `reticle_clock`, `reticle_network_mock`, `reticle_storage`.
- Other wire names, many reached via `reticle_run({tool,…})`: `reticle_look`, `reticle_lineage`, `reticle_animations`, `reticle_reconcile`, `reticle_baseline_*`, `reticle_diff`, `reticle_record_start/stop`, `reticle_replay`, `reticle_explore`, `reticle_flow_list/load/delete/verify/heal/mutate/save_recorded`, `reticle_annotate`, `reticle_affected`, `reticle_coverage`, `reticle_verify_change`, `reticle_verify_explore`, `reticle_contract_save`, `reticle_domain`, `reticle_project`, `reticle_account`, `reticle_memory`, `reticle_run_record`, `reticle_run_export`, `reticle_crawl`, `reticle_lease*`, `reticle_tools`, and more.

### Headless / CI invocation (VERIFIED, README.md:543; docs/cli/verify.mdx; action/action.yml)
- Run `npx @reticlehq/server verify <url> --results-json r.json`, then `npx @reticlehq/server gate --since HEAD~1`.
- Headless is the default; `--headed` opts out.
- The app must be running with the SDK built in. There is no daemon conflict in CI.
- `action/action.yml` is a composite GitHub Action. It **requires `api-key`** (a Reticle workspace key), so it is platform-tied. The raw CLI works without an account.

### Runtime requirements (VERIFIED)
- Node ≥20.11 (server/package.json engines; README.md:117).
- Playwright is an optionalDependency `^1.63.0` and an optional peer `>=1.50`, with a bundled-Chromium launcher (server/src/launch-chromium.ts).
- The app must be a JS web app (React/Vite/Next/Remix/Astro are proven in CI per README.md:749) or an Electron/Tauri desktop app.
- Port 4400 on loopback. Pairing token in `~/.reticle/`.

### Integration recommendation
**Optional RuntimeVerifier plugin via subprocess, never a dependency.**
- **Detect:** `.reticle.json` or `.reticle/flows/` exists in the repo.
- **Plan:** call `reticle affected --since <base>` to learn which flows a change touches. Its JSON output is the useful signal.
- **Verify:** call `reticle verify <url> --results-json f` and/or `reticle gate --since <base>`.
- **Map** the results onto RepoMarshal's statuses:

  | Reticle signal | RepoMarshal status |
  |---|---|
  | `yes`, gate exit 0 | PASS |
  | `no`, gate exit 1 | FAIL |
  | `unknown` | UNKNOWN |
  | gate exit 2 (no flows), or no session / engine failure on verify exit 1 | BLOCKED or UNKNOWN |

  **Caveat:** verify's single exit code 1 conflates FAIL with BLOCKED, so parse `--results-json` and stderr. Set `RETICLE_TELEMETRY=0` / `DO_NOT_TRACK=1` on every invocation to honour local-first.
- **Do not** auto-run `reticle init`; it modifies the user's app and agent files. Do not vendor or embed server code (FSL).
- Studying and reimplementing OVP semantics (Apache-2.0) is fine with attribution.

### Attribution obligations
- **Apache-2.0 packages:** if any code from them is redistributed, keep the LICENSE and the NOTICE "Reticle / Copyright 2026 Reticle Labs".
- **FSL-1.1-ALv2 server/CLI:**
  - Invoking it as a user-installed tool is a Permitted Purpose (INFERRED).
  - RepoMarshal must not bundle or redistribute it as part of a competing commercial verification product.
  - RepoMarshal must not copy server code.
- **OVP concepts:** cite "Open Verification Protocol (reticlehq, Apache-2.0)" if the vocabulary or ordering is adopted.

### Compatibility concerns
- Requires SDK instrumentation of the target app; most repos will not have it.
- The JS/desktop-app domain only.
- Fast release cadence (v3.2 to v3.6 in weeks).
- `--results-json` is a narrow but documented contract. Gate and affected output are single JSON lines whose `event` names are documented but that carry no schema version.
- Telemetry is on by default.
- The README states Node 20.11+, but monorepo development needs 22.12.
- Platform-coupled features (Harness, `runs regression`, the GH Action) need an account.

### Strengths
- Rigorous verdict semantics: `unknown` and `no-fault` are first-class, and there is a normative adjudication order.
- An Apache-2.0 protocol spec with conformance vectors.
- Deterministic replay without an LLM.
- Change-aware `affected` and `gate`.
- Per-journey machine-readable results.
- Loopback-only design.

### Weaknesses
- Intrusive setup.
- Narrow target domain.
- Mixed licensing (FSL server, enterprise `ee/`).
- Telemetry on by default.
- CI Action needs a SaaS key.
- verify exit 1 overloads fail with infrastructure errors.

---

## 3. microsoft/playwright

| Field | Value |
|---|---|
| Identifier | github.com/microsoft/playwright (clone dir `microsoft__playwright`) |
| SHA | `a3401cfe0dcf317101572d025fa46248d0779b42` |
| Last commit | 2026-10-12 16:44:45 -0700 ("feat(webkit): roll to r2375 (#43265)") |
| Tags / history | **UNKNOWN.** The clone is shallow (`git rev-list --count HEAD` = 1) and has no tags. Root package.json version is `1.65.0-next` (an unreleased dev line) |
| Checkout | Sparse. I added `packages/playwright/src/{agents,cli,program.ts,reporters/json.ts}`, `types/testReporter.d.ts`, `packages/playwright-core/src/{tools,cli,entry/mcp.ts}` |

### License (VERIFIED)
- `LICENSE` is Apache-2.0.
- `NOTICE` reads "Playwright / Copyright (c) Microsoft Corporation" and states it contains code derived from Puppeteer (Apache-2.0) (NOTICE:1-5).
- `packages/playwright/package.json` and `packages/playwright-core/package.json` both have `"license": "Apache-2.0"`.
- CONTRIBUTING/CLA was not inspected (sparse checkout). **INFERRED:** Microsoft CLA, which is irrelevant for consumers.

### Agent / MCP tooling in this repo (VERIFIED)
- **The `packages/playwright-mcp` directory does not exist at this SHA.** The MCP server implementation lives in `packages/playwright-core/src/tools/mcp/*` and `tools/backend/*`.
- It is exposed as `playwright-core mcp` (packages/playwright-core/src/cli/program.ts:249-251) and as the `entry/mcp.ts` program ("Playwright MCP").
- **INFERRED:** the npm `@playwright/mcp` package is now a thin wrapper published elsewhere. Not verifiable here.
- Browser MCP tools (grep of `name: '…'` in `playwright-core/src/tools/backend`):
  - navigation: `browser_navigate`, `browser_navigate_back`, `browser_navigate_forward`, `browser_reload`
  - page reading: `browser_snapshot`, `browser_find`, `browser_generate_locator`
  - interaction: `browser_click`, `browser_type`, `browser_fill_form`, `browser_hover`, `browser_drag`, `browser_drop`, `browser_select_option`, `browser_check`, `browser_uncheck`, `browser_press_key`, `browser_press_sequentially`, `browser_keydown`, `browser_keyup`, `browser_mouse_*`, `browser_file_upload`, `browser_handle_dialog`
  - verification: `browser_verify_element_visible`, `browser_verify_text_visible`, `browser_verify_list_visible`, `browser_verify_value`, `browser_wait_for`
  - code execution: `browser_evaluate`, `browser_run_code_unsafe`
  - console and network: `browser_console_messages`, `browser_console_clear`, `browser_network_requests`, `browser_network_request`, `browser_network_clear`, `browser_network_state_set`, `browser_route`, `browser_route_list`, `browser_unroute`
  - storage: `browser_cookie_*`, `browser_localstorage_*`, `browser_sessionstorage_*`, `browser_storage_state`, `browser_set_storage_state`
  - capture: `browser_take_screenshot`, `browser_pdf_save`, `browser_start/stop_tracing`, `browser_start/stop_video`, `browser_video_*`, `browser_start/stop_recording`
  - session and display: `browser_tabs`, `browser_close`, `browser_resize`, `browser_emulate_media`, `browser_resume`, `browser_get_config`, `browser_highlight`, `browser_hide_highlight`, `browser_annotate`
  - page-exposed MCP: `browser_webmcp_list`, `browser_webmcp_call`
- **Test-runner MCP server:**
  - Hidden command `npx playwright run-test-mcp-server [--headless] [-c config] [--host] [--port]` (packages/playwright/src/program.ts:140-159).
  - Tools: `test_list`, `test_run`, `test_debug`, `planner_setup_page`, `planner_submit_plan`, `planner_save_plan`, `generator_setup_page`, `generator_read_log`, `generator_write_test` (packages/playwright/src/mcp/test/*Tools.ts).
  - `test_run(locations?, projects?)` returns **plain text**, not structured results (testTools.ts:40-57).
- **Agents:**
  - `npx playwright init-agents --loop claude|codex|copilot|opencode|vscode|vscode-legacy [-c] [--project] [--prompts]` writes planner, generator and healer agent definitions, e.g. `.claude/agents/*.md` or `.vscode/mcp.json` (program.ts:161-185; src/agents/*.agent.md, generateAgents.ts).
  - `npx playwright init-skills --loop claude|agents` installs skills (`playwright-cli`, `playwright-component-testing`, `playwright-trace`, in `playwright-core/src/tools/skills/*/SKILL.md`) (program.ts:187-200).
- A `playwright-core cli` command (tools/cli-client) and `trace` subcommands also exist (cli/program.ts:236-247).

### Most stable machine-readable contract for a RuntimeVerifier
- **Exit codes** (VERIFIED, packages/playwright/src/cli/testActions.ts:86-87): `status === 'passed'` gives **0**, `interrupted` gives **130**, anything else (failed, timedout) gives **1**.
- Related flags (docs/src/test-cli-js.md:85-101):
  - `--pass-with-no-tests`, because no tests found is otherwise a failure;
  - `--fail-on-flaky-tests`;
  - `--only-changed [ref]`, which is git-based change-aware selection;
  - `--last-failed`;
  - `--list`.
- **JSON reporter:** `npx playwright test --reporter=json`. Output goes to stdout by default, or to a file via `PLAYWRIGHT_JSON_OUTPUT_FILE` / `PLAYWRIGHT_JSON_OUTPUT_NAME` / `PLAYWRIGHT_JSON_OUTPUT_DIR` or the config `outputFile` (docs/src/test-reporters-js.md:409-445).
- **JSON schema** (TypeScript types in packages/playwright/types/testReporter.d.ts:269-360, a public types file):
  - `JSONReport {config, suites: JSONReportSuite[], errors: TestError[], stats{startTime, duration, expected, unexpected, flaky, skipped}}`
  - Suite: `{title, file, line, column, specs[], suites?[]}`
  - Spec: `{tags, title, ok, tests[], id, file, line, column}`
  - Test: `{timeout, annotations, expectedStatus, projectName, projectId, results[], status: 'skipped'|'expected'|'unexpected'|'flaky'}`
  - Result: `{workerIndex, parallelIndex, shardIndex?, status, duration, error, errors[], stdout[], stderr[], retry, steps?, startTime, attachments[{name,path?,body?,contentType}], annotations, errorLocation?}`
  - **The report has no schema-version field.** The Playwright version is available in `config.version` (INFERRED from FullConfig) and the format is versioned implicitly by the Playwright release.
- **JUnit reporter** is an alternative (stable XML).
- **Recommended mapping** (INFERRED):

  | Playwright result | RepoMarshal status |
  |---|---|
  | `stats.unexpected > 0` | FAIL |
  | exit 0 and `unexpected == 0` and `expected > 0` | PASS |
  | `expected == 0` with everything skipped, or exit 1 with top-level `errors[]` non-empty and no tests run (config or webServer failure) | BLOCKED or UNKNOWN |
  | exit 130 | BLOCKED (interrupted) |
  | `flaky` | Report it; policy decides |

### Integration recommendation
- Use a subprocess adapter: `npx playwright test --reporter=json [--only-changed=<base>]` with `PLAYWRIGHT_JSON_OUTPUT_FILE`.
- Parse `stats` and the per-test `status`, and attach the trace and screenshot paths from `attachments` as evidence.
- Do not use the MCP servers for verification evidence. They are agent-interactive and `test_run` returns unstructured text.
- RepoMarshal can recommend `init-agents` / Playwright MCP as context or skills when a repo has `playwright.config.*`.

### Attribution obligations
- Apache-2.0. Subprocess invocation creates none.
- If any code or types are copied: keep the LICENSE and NOTICE (Microsoft plus the Puppeteer-derived notice).
- Re-typing the JSON report interfaces for parsing is an interface description, a low-risk practice (INFERRED). Cite the source file anyway.

### Compatibility concerns
- Requires the project's own `@playwright/test` version. The reporter shape differs subtly across versions (e.g. `errors[]` vs `error`, `shardIndex`, `steps`). Fields should be optional-tolerant.
- Needs browsers installed (`npx playwright install`).
- `1.65.0-next` here is unreleased. RepoMarshal should parse whatever version the target repo pins.

### Strengths
- Ubiquitous.
- Clear exit codes.
- Typed, public JSON report types.
- Built-in change-aware `--only-changed`.
- Rich evidence artifacts (trace, video, screenshots).
- First-party MCP and agent scaffolding.

### Weaknesses
- No explicit UNKNOWN concept: skipped and errors must be interpreted.
- No schema version.
- MCP test tools return text.
- Agent and MCP features churn quickly; the package was relocated into playwright-core.

---

## Cross-cutting conclusions for RepoMarshal
1. **Use subprocess adapters for all three.** Each has a documented CLI exit-code contract plus a JSON output. None exposes a stable, versioned library API: SkillSpector's Python API is a LangGraph state dict, Reticle's server is FSL, and Playwright's MCP test tools return text. (INFERRED, from the VERIFIED facts above.)
2. **None of the three JSON formats carries a top-level schema version.** RepoMarshal adapters should record the tool version and validate the field subset they need. (VERIFIED for SkillSpector and Playwright. For Reticle, the `--results-json` shape has no version field per verify-results.ts.)
3. **Reticle's OVP is the strongest prior art** for RepoMarshal's PASS/FAIL/UNKNOWN/BLOCKED semantics, and it is Apache-2.0:
   - `unknown` never collapses into a pass or a fail;
   - `no-fault` (nothing to prove) is distinct from `unknown`;
   - "couldn't see" is evaluated before "didn't happen";
   - the summary must be recomputable from the verdicts.
4. **Local-first egress must be controlled:**
   - SkillSpector LLM: always pass `--no-llm`.
   - SkillSpector OSV: unavoidable without a network sandbox.
   - Reticle telemetry: set `RETICLE_TELEMETRY=0` / `DO_NOT_TRACK=1`.
5. **Change-aware selection already exists upstream:** `reticle affected/gate --since` and `playwright test --only-changed`. RepoMarshal can delegate test selection to them when present.
