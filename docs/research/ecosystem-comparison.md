# Ecosystem comparison

_Phase 1 research snapshot, inspected 2026-10-13. Commit SHAs are in
[`../UPSTREAM_PROVENANCE.md`](../UPSTREAM_PROVENANCE.md). The `path:line` evidence for every claim is in
[`notes/`](notes/)._

**Method.** For each canonical repository we read the README, LICENSE/NOTICE files and per-package license
fields, the package metadata, the source layout and CLI implementation, the public interfaces, the
tests, the schemas, and the tags and recent history.

- **Tools we ran:** SkillSpector (static mode, offline) and UI/UX Pro Max's search script, to check
  behaviour first-hand.
- **Not inspected:** GitHub issues and star counts. The GitHub API was unreachable from the research
  environment.
- **Projects found only by web search** are marked _(web)_. They were not cloned, so their claims are
  less certain.

Labels used below: **V** = verified in source or by running the tool; **I** = inferred.

---

## 1. State-of-the-art matrix

| Project | Core problem | Key mechanism | Strength | Weakness | RepoMarshal overlap | Integration opportunity | License |
|---|---|---|---|---|---|---|---|
| **NVIDIA SkillSpector** | Is this agent skill malicious or unsafe? | Static regex/AST/taint/YARA plus OSV supply-chain lookup, with an optional LLM pass (LangGraph). Outputs JSON and SARIF 2.1.0 (V) | Clear exit-code contract. `analysis_completeness` reports how much of the target was actually inspected. Never executes the scanned skill (V) | 244 MB install, Python ≥3.12. No schema version. CAUTION exits 0. OSV egress can't be disabled. Misses risky MCP client configs (V, probed) | Low: RepoMarshal must not build a scanner | **Optional security provider via subprocess** (`scan --no-llm --format json`) | Apache-2.0 |
| **Reticle** | Did the app *actually* behave as claimed at runtime? | In-app dev SDK, a local daemon and an MCP server. Saved JSON flows replay without an LLM. The Open Verification Protocol defines verdicts yes / no / unknown / no-fault (V) | The most rigorous verdict semantics found: `unknown` never collapses into pass or fail (V). Includes `affected` and `gate --since` for change-aware flow selection (V) | Needs SDK instrumentation of the app (JS web or desktop only). Telemetry is on by default. `verify` exit 1 covers both real failures and setup errors (V) | Medium: verdict semantics and change-aware flow selection | **Optional runtime provider via subprocess**, only when `.reticle/` exists. Reimplement the OVP verdict ideas (Apache-2.0 spec) with credit | Mixed: spec, core, SDK and adapters Apache-2.0; server and `reticle` CLI **FSL-1.1-ALv2**; `ee/` proprietary |
| **Chisel** | Give agents durable project knowledge (specs and docs) | Markdown plus YAML specs with a lifecycle, a SQLite FTS index, and `context create <query>` (V) | Simple file format. Explicit draft → shipped lifecycle (V) | Not open source (FSL). Reads can mutate the repo (auto-migration, index.db). Docs disagree with the code. No MCP (V) | Low to medium: "context" is query-driven, not driven by the change | **Read `.chisel/specs/*.md` with our own parser** (planned, optional). Never vendor or bundle Chisel code | **FSL-1.1-Apache-2.0** (each release converts after 2 years) |
| **UI Skills** | Which UI skill should the agent load? | Remote registry of 395 third-party SKILL.md files in 26 topics. Progressive-disclosure CLI and a remote MCP server (`list_skills`, `get_skill`) (V) | Simple disclosure protocol. "Prefer 1 skill, never more than 3" (V) | **Selection is LLM judgement written as prose. No code routing** (V). Network-only. Skill content is not pinned to a commit | Medium on the skill-routing idea | **Optional remote skill catalog** (network opt-in). Generalise the ≤3-skill idea into deterministic routing | MIT (each third-party skill has its own license) |
| **UI/UX Pro Max** | UI design knowledge for agents | 3.1 MB of CSV and JSON (4,319 rows) searched with stdlib BM25. Score floors make it **return nothing** rather than a weak match (V) | Deterministic, offline, honest abstention. Data provenance and freshness tracking (V) | Mostly subjective taste data. Stack must be passed by hand (no detection). One bundled skill has conflicting licenses (V) | Low | **Optional advisory provider**: when UI files change, query with the stack RepoMarshal detected. Results are advice only, never PASS/FAIL | MIT (`ui-styling/` ships Apache-2.0 and OFL files) |
| **AgentProof** | Did the agent's change really fix the problem, without gaming the tests? | Runs one test command in clean BASE and HEAD worktrees. "Proof tests" must fail before and pass after. Tamper rules AP001–AP502 flag deleted tests, new skips, narrowed test discovery and CI changes. Receipt `agentproof.receipt/v1` (V) | Real before/after comparison. Stable rule IDs. Honest threat model (V) | One fixed test command. Regex heuristics. Built in about 14 hours (release candidates only). Coverage and mutation checks are stubs (V) | High on completion evidence; **none on selecting gates** | **Optional "test integrity" gate provider**, selected when tests, CI or lockfiles change. Consume its receipt | Apache-2.0 |
| **AgentWard** | Enforce least privilege on agent tool calls | stdio MCP proxy and HTTP proxy with a pydantic policy (`agentward.yaml`). JSONL audit log with optional HMAC chain (V) | Enforcement happens outside the model. Rich constraint language (V) | **Doesn't see built-in agent tools** (Claude/Codex Edit and Bash). Dormant since 2026-04-28. Relicensed to BUSL (V) | Low | **Out of scope for RepoMarshal**. At most, export the plan's allowed write scope as policy data | **BUSL-1.1** since 2026-04-24 (earlier commits Apache-2.0) |
| **AgentSmith** | Whole harness: rules, profiles, definition of done, verification, handoffs | `verify discover` writes a *repo-level* `verification-plan-v1`: 8 gate categories, each proposed gate with reason and confidence, coverage states including `not-applicable`. `verify` runs **all** phases and stops at the first failure. Receipts hash outputs (V) | Very active (93 commits in September). About 421 tests. 16-agent registry. Strong anti-tamper in autonomous mode (V) | **Planning takes no diff, base or task** (`build_verification_plan(target)`). Adapting to the change exists only as prose for the LLM (V). Rule R5: "run the full check … not just the one thing you touched" | **Highest**: gate categories, reasons, receipts, multi-agent | Read `.harness/verify.conf` or its plan JSON as a gate inventory. Run selected labels | MIT |
| **doidor/agentrig** | One canonical agent harness for every host | **Since 2026-10-01: a written spec only (AGENT-RIG.md 2.0) plus a static validator. The CLI was removed.** The spec says "avoid a compiler" for host projection (V) | Clear minimum contract (`AGENTS.md ## Commands`, rules with `globs` and `priority`) (V) | Architecture volatile. One maintainer. No API (V) | Low | **Read** `AGENTS.md` Commands and `.agents/rules` globs as policy input. Don't build a projection engine | MIT |
| **regenrek/agentrig** | Distribute skills and MCP servers to agents with trust | Agent Plugins 1.0 (Working Draft) packages. Signed static registries, trust tiers, hash-owned install ledger. Materializes for Claude, Codex and Cursor (V) | Standards-first trust and provenance model (V) | Draft spec. 3 hosts. No commits for 2 months (V) | Low | **Ship RepoMarshal as an Agent Plugins package** (SKILL.md plus a stdio `mcp.json`) and let AgentRig install it | MIT |
| **DoneProof** | Honest completion claims | Receipt schema (`urn:doneproof:schema:receipt:1.0`): task, changed files, commands with passed/failed/skipped and reason, evidence, risks. No "done" status allowed (V) | Clear human-review stance (V) | **Everything is self-reported. Nothing is executed or cross-checked against the diff.** Low activity (V) | Low | **Export a DoneProof receipt** from RepoMarshal's verified evidence | MIT |
| **Preloop** (discovered) | Agent control plane (MCP firewall, flows, approvals) | **PR #435: hand-written `VerificationProfile` with `always`, path-glob `rules` and a never-empty `unknown_default` selects required checks from the diff.** Fail-closed: evidence must come from the trusted verifier for the exact commit and tree. The profile lives outside the repo under test (V) | **The closest existing implementation of diff → required checks for agents** (V) | Profile written by hand. Path globs only. Command exit codes only. No reasons for skipped checks, no context or skills. Embedded in a server platform (V) | **High on gate selection** | Export a RepoMarshal plan as a Preloop profile. Adopt the trusted-policy and fail-closed ideas (Apache-2.0) | Apache-2.0 |
| **Playwright** | Browser automation and testing | `playwright test --reporter=json` (typed public report), exit codes 0/1/130, `--only-changed`. MCP now lives in playwright-core. `init-agents`/`init-skills` (V) | Used everywhere. Stable typed report. Rich artifacts (V) | No UNKNOWN concept. No schema version. MCP `test_run` returns plain text (V) | Low | **Primary deterministic runtime/browser provider** via subprocess and the JSON reporter | Apache-2.0 |
| **MCP specification** | Interop between agents and tools | Latest release **2026-07-28**: stateless (no `initialize`). Structured `outputSchema`. Tasks and Skills are official extensions. Roots, Sampling and Logging deprecated (V) | Supported by every target agent host (I) | Big breaking revision. SDKs and clients lag (I) | — | **RepoMarshal's agent-facing interface**: stdio server with read-only tools, explicit `repo_path`, `plan_id` handles | Moving MIT → Apache-2.0; docs CC-BY-4.0 |

### Other relevant discoveries (web sweep; see `notes/additional-discoveries.md`)

| Project | Relevance | Status / license |
|---|---|---|
| GuardSpine Code Action _(web)_ | Rates each diff L0–L4 from paths, keywords and size. "Only deterministic findings can block; AI findings only add conditions." Hash-chained evidence bundle | BSL 1.1; low adoption; study only |
| agent-pr-reviewer-lite _(web)_ | Deterministic diff rule table. Flags agent config files (`.claude/settings.json`, `.mcp.json`), CI, deleted tests and lockfiles as high risk | MIT; tiny |
| agent-pr-gate, groundtruth, cngx, verify-mcp-server, GUILDLESS, loop-hooks _(web)_ | Check agent claims or completion after the fact. Several have an "unchecked is never a pass" state. loop-hooks shows the Claude Code `Stop` hook integration point | MIT; all very early (0–3 stars) |
| Nx affected, Turborepo, bazel-diff, Pants, Jest `--findRelatedTests`, pytest-testmon, Playwright `--only-changed`, Reticle `affected` | Mature **test or target** selection from a diff. None of them selects different kinds of gate (secrets, deps, UI, runtime) or explains skips | Mostly MIT/Apache; delegate to them via adapters |
| CodeRabbit pre-merge checks, Vercel PR risk classifier, MergeShield, Meta RADAR, CloudBees Smart Tests _(web)_ | Industry confirmation that risk-tiered handling of AI changes is wanted. All LLM-based, post-PR review or merge routing | Proprietary |
| Snyk Agent Scan (ex mcp-scan), Cisco skill-scanner _(web)_ | More skill and MCP scanners. A CSA note (2026-06-10) reports Trail of Bits bypassed **all** tested skill scanners | Treat any scanner pass as evidence, not proof |
| chiruu12/marshal _(web)_ | Coding-agent fleet orchestrator with a `marshal` CLI | Name-adjacent only (see §4) |

---

## 2. What each project teaches the RepoMarshal design

| Lesson | Source | Consequence for RepoMarshal |
|---|---|---|
| "Unknown impact never resolves to an empty check list" | Preloop `unknown_default` (V) | A changed file the rules don't cover always triggers a conservative fallback, never a smaller plan |
| The policy that decides required checks must not be editable by the change it judges | Preloop trusted profile (V); AgentSmith `VERIFICATION_POLICY_PATHS` (V) | **Read policy from the BASE commit.** A diff that edits `repomarshal.yaml`, CI or agent config is itself a high-risk signal and cannot relax its own plan |
| Evidence is only what the trusted runner produced, bound to exact commit and tree | Preloop `evaluate_from_raw` (V); AgentProof receipts (V) | Agent-reported results are claims. Evidence records include commit, tree hash, plan hash, policy hash and tool version |
| `unknown` must not collapse into pass or fail. "Nothing to prove" is not a pass | Reticle OVP §7 (V) | Distinguish `NOT_APPLICABLE` (skipped, with a reason) from `PASS`, and `UNKNOWN` from both |
| Abstain rather than guess | UI/UX Pro Max BM25 score floors (V); SkillSpector `analysis_completeness` (V) | Skill and context ranking may return "no confident match". Incomplete scanner coverage maps to UNKNOWN |
| Run cheap checks everywhere, and select only expensive ones | Preloop `always` (V); AgentSmith R5 (V) as the counter-argument | Minimisation applies to *expensive* gates (runtime, browser, integration, deep scans) and is escalated on shared or CI changes. Cheap lint and typecheck stay on |
| Skill routing written as prose is not reproducible | UI Skills `ui-skills-root/SKILL.md` (V) | Deterministic routing from changed paths, detected stack and skill frontmatter, with the reason recorded |
| Generating per-host agent config is an unstable, crowded area | doidor/agentrig dropped its compiler (V); regenrek hard-cut to v2 (V) | Don't own agent config files. Print snippets; ship as an Agent Skills / Agent Plugins package plus an MCP stdio server |
| Providers' JSON outputs carry no schema version | SkillSpector, Playwright, Reticle (V) | Adapters pin tested version ranges, validate only the fields they consume, and record the tool version in evidence |
| Local-first takes active effort | SkillSpector OSV egress (V); Reticle telemetry on by default (V); UI Skills network-only (V) | Dry-run shows network-touching steps. Pass `--no-llm` and `RETICLE_TELEMETRY=0`. Network providers are opt-in |

---

## 3. Native vs provider: revised after research

| Capability | Strategy | Why |
|---|---|---|
| Git analysis (base, head, changed files, renames, content diffs) | **NATIVE** | Core input; small; no adequate library contract upstream |
| Change classification (domains, risk signals) | **NATIVE** | No inspected tool does this deterministically. AgentSmith does it in prose, Preloop with globs only |
| Plan compilation (context, skills, gates, evidence policy, explanations) | **NATIVE** | The product |
| Gate inventory detection (which lint, typecheck and test commands exist) | **NATIVE** | Needed with no configuration. AgentSmith `verify discover` is prior art for the idea; we can also *read* AgentSmith's `verify.conf` and `AGENTS.md ## Commands` |
| Gate execution, evidence ledger, completion verdict | **NATIVE** | Must be trusted and bound to the plan. Small |
| Context selection | **NATIVE (deterministic heuristic, labelled as such)** | Path, import adjacency, tests and docs/ADRs. Optional Chisel spec reader **PLANNED** |
| Skill recommendation from local skills | **NATIVE** | Deterministic routing over SKILL.md frontmatter. UI Skills remote catalog **OPTIONAL PROVIDER**, network opt-in |
| Deep skill / MCP security scanning | **OPTIONAL PROVIDER: SkillSpector** | Don't rebuild. With no scanner available the result is **UNKNOWN**, never ALLOW |
| Test-tamper and before/after differential proof | **OPTIONAL PROVIDER: AgentProof**; native *diff signals* only (deleted tests, new skips, CI narrowing raise risk) | Detecting these as cheap risk signals is in scope. Before/after execution is AgentProof's job |
| Browser / runtime verification | **PROVIDER: Playwright** (JSON reporter); **OPTIONAL PROVIDER: Reticle** when `.reticle/` exists | Deterministic, widely installed. Reticle only where already instrumented |
| UI design knowledge | **OPTIONAL ADVISORY PROVIDER: UI/UX Pro Max** | Subjective. Never PASS/FAIL evidence |
| Test-level selection inside a gate | **DELEGATE** (Playwright `--only-changed`, Jest `--findRelatedTests`, Nx affected) — **NOT NOW** for v0.1 | Mature upstream. v0.1 picks gates, not individual tests |
| Runtime tool-call enforcement, sandboxing | **OUT OF SCOPE** | AgentWard, Preloop, MCP gateways and host hooks do this. RepoMarshal does not claim to enforce or sandbox |
| Agent-specific config projection | **OUT OF SCOPE** (print snippets only) | AgentRig and the hosts' own plugin systems |
| Skill distribution, registries, trust tiers | **OUT OF SCOPE** | regenrek/agentrig, skills.sh |
| Receipt formats for other ecosystems | **EXPORT** (DoneProof receipt, Preloop profile, SARIF) — **LATER** | Interop, not competition |

---

## 4. Name check

- **Packages:** `repomarshal` and `repo-marshal` are **unclaimed on PyPI, npm and crates.io**, and so is
  `@repomarshal/cli` on npm (HTTP 404, 2026-10-13).
- **GitHub and the web:** "repomarshal" has no hits; "repo-marshal" finds only this project.
- **Trademark:** no formal USPTO or EUIPO search was run (UNVERIFIED).
- **Avoid a bare `marshal` name** for the CLI, package or import:
  - chiruu12/marshal has a `marshal` CLI in the same AI-coding-agent space;
  - crates.io `marshal` was reserved in 2026-04 for git tooling;
  - Python's standard library has a `marshal` module.

**Verdict:** keep the name. Use `repomarshal` for the CLI and import name.
