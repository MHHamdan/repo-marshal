# RepoMarshal: additional prior-art discoveries (web sweep, 2026-10-13)

Method: WebSearch + WebFetch of GitHub/registry pages, plus direct registry HTTP checks (crates.io, PyPI, npm).
`gh` and api.github.com were blocked in this session (repo access not enabled), so star counts come from
rendered GitHub pages (fetched 2026-10-13) or third-party aggregators. Last-commit dates were mostly NOT visible
in fetched pages; they are marked UNVERIFIED. No commit SHAs recorded (no clones made); record SHAs when
cloning for Phase 1 deep-dives.

Legend: Overlap = how much it duplicates RepoMarshal (diff+task+policy -> minimum plan of context/skills/gates,
explained selections/skips, evidence, PASS/FAIL/UNKNOWN/BLOCKED). Opp = integration opportunity.

---

## 6. MOST IMPORTANT: diff -> risk -> required checks / definition-of-done for agents

Bottom line: no project found compiles a diff + task + policy into a *selected-and-explained gate plan with
context and skills* for an agent. Existing tools split into (a) diff -> risk tier -> review routing,
(b) static required-checks list + attestation, (c) "verify the agent's claims" gates, (d) change-gated hooks
that run one fixed command. RepoMarshal's combination (selection + skip explanation + evidence + 4-state verdict,
local-first, pre-work planning not just post-hoc gating) appears unoccupied. Several small projects are close
on individual pieces.

### GuardSpine Code Action (closest on "diff -> risk tier -> required verification")
- URL: https://github.com/DNYoussef/guardspine-code-action
- What: "AI-aware code governance with cryptographically verifiable evidence bundles". GitHub Action.
- Mechanism: parses unified diff; risk tier L0-L4 = max(path-pattern score, sensitive-zone keyword score in diff,
  change size), overridable via `risk_policy` YAML. Tier decides how many AI reviewers (0-3) and rubric depth
  (SOC2/HIPAA/PCI rubrics). Decision engine: merge / merge-with-conditions / block; only deterministic
  ("provable") findings can block, AI "opinion" findings only add conditions. Emits hash-chained JSON evidence
  bundle (guardspine-spec v0.2.x), optional signatures, SARIF; separate `guardspine-verify`.
  README includes setup instructions aimed at Claude Code/Codex/Gemini CLI/Aider.
- Signals: 6 stars, 3 forks, 131 commits. Upstream `guardspine-product` frozen 2026-04-04. Releases empty.
- License: Business Source License 1.1 (converts to Apache-2.0 after 4 years) -> NOT reusable code for an OSS
  project; study ideas only.
- Overlap: HIGH on risk tiering + evidence bundle + deterministic-blocks/AI-advises split. Does NOT select
  lint/typecheck/test gates, no context/skills, no UNKNOWN state, CI-side not local-first.
- Opp: borrow the "provable vs opinion" severity rule and hash-chained evidence idea; could emit a compatible
  evidence format. Low adoption.

### Preloop PR #435 "Gate implementation publication on selected checks"
- URL: https://github.com/preloop/preloop/pull/435 (merged 2026-09-06, commit c762ff2)
- Repo: https://github.com/preloop/preloop - "The open-source AI agent control plane". 77 stars, 17 forks,
  ~7.1k commits, Apache-2.0 (c) 2026 Spacecode AI Inc. Active (PR merged Sep 2026).
- Mechanism (PR): diff-based verification profiles, required-command selection, pre-publication gate that blocks
  push/PR creation when a required check fails or cannot run; malformed policy or changed code with no
  matching rule cannot silently bypass (fail-closed). Notes in-sandbox verification records are
  `authenticated: false`; trusted verifier tracked in issue #432.
- Overlap: HIGH on "diff -> selected required checks -> fail-closed" for agent output. This is the single
  closest mechanism found. Embedded in a larger server platform (not a local planner); no skip explanations
  or context/skills planning found (UNVERIFIED - only PR summary read).
- Opp: read PR #435 code + issue #432 in Phase 1 (record SHA c762ff2). Preloop could consume a RepoMarshal
  plan as its verification profile; also see section 5 (it is an MCP firewall too).

### agent-pr-gate
- URL: https://github.com/zhengqiuyang/agent-pr-gate - MIT, 0 stars, 4 commits (very early).
- Mechanism: deterministic CI gate for AI-agent PRs, "No LLM in the trust path". 4 pillars: scope policy
  (allow/deny globs; policy selected by PR label, else best path-match, else default), residue scan of added
  lines (prompt-injection phrasing, disabled tests, stubs, hidden Unicode; gitleaks advisory/"skipped"),
  run attestation (named check runs must succeed on head SHA), claims-vs-diff (PR body claims ->
  verified/mismatched/unverified). Exit 0/1/2.
- Overlap: MEDIUM-HIGH conceptually (policy selection by diff, "unverified" never silently passes, "skipped"
  pillar). Required checks are a static list, not per-diff selection.
- Opp: good reference for claim-vs-diff and label/path policy selection. Too early to depend on.

### agent-pr-reviewer-lite
- URL: https://github.com/alipajand/agent-pr-reviewer-lite - MIT, 1 star, 104 commits. Not on npm by design.
- Mechanism: deterministic, no LLM; diff between refs -> path/added-line rules -> severity (high: auth, billing,
  migrations, deleted tests, CI workflows, `.claude/settings.json`, `.mcp.json`, CODEOWNERS, secrets,
  `.only`/skip; medium: lockfiles, .env, infra, lint suppressions, new deps). Outputs text/JSON/MD/SARIF/JUnit/
  GitHub annotations; `--fail-on` exit gate; maps to CODEOWNERS.
- Overlap: MEDIUM - essentially RepoMarshal's diff-classification rule table, without gate planning.
- Opp: its rule list is a good seed for RepoMarshal's risk-signal catalogue (agent-config files as high risk).

### groundtruth
- URL: https://github.com/MSal2020/groundtruth - MIT, 0 stars, 14 commits.
- Mechanism: deterministic/offline; inspects `git diff HEAD` (+untracked; PR base in Action), reruns tests,
  checks agent claims, test-harness gaming, stubs, dependency existence (npm/PyPI/Go proxy), builds.
  Verdicts: overclaim (DO NOT MERGE), *unchecked* (could not run - "not folded into a pass"), pass.
  Runs as CLI, Claude Code Stop hook, GitHub Action.
- Overlap: MEDIUM on evidence + UNKNOWN-like "unchecked" semantics. No plan selection.

### cngx
- URL: https://pypi.org/project/cngx/ (repo https://github.com/aadi-joshi/cngx) - MIT, v0.2.0 2026-07-10.
- Mechanism: re-runs the check command the agent claimed, compares parsed result to claim; VERIFIED/BLOCKED.
  Offline `--evidence-file`. Stores traces in `.cngx/` (DuckDB). User supplies the command; no selection.
- Overlap: LOW-MEDIUM (evidence/claim verification only). Opp: complementary post-run verifier.

### verify-mcp-server
- URL: https://github.com/Pappet/verify-mcp-server - MIT, 0 stars, 42 commits.
- Mechanism: MCP server; agent writes a contract up front (`verify_create_contract`), later
  `verify_run_contract`; 17 check types (`command_succeeds`, `ast_query`, `diff_size_limit`, ...);
  language meta-validation forces certain checks for Py/Rust/JS/TS; unproven assertions -> Unverified ->
  Review Required. Verdicts Passed/Failed/Review Required.
- Overlap: MEDIUM on "definition of done as MCP + contract + verdicts". Checks are agent-authored, not derived
  from diff/policy. Opp: RepoMarshal could generate such contracts deterministically.

### GUILDLESS
- URL: https://github.com/guildless-ai/guildless - MIT, 1 star, 14 commits; README in Japanese.
- Mechanism: `guildless verify` fail-closed completion gate: tested SHA == HEAD, clean tree, configured commands,
  URLs, declared "unverified scope". Overlap: LOW-MEDIUM (completion evidence). Diff-driven selection UNVERIFIED.

### loop-hooks
- URL: https://github.com/wwwcojp/loop-hooks - MIT, 0 stars, 159 commits. Claude Code plugin.
- Mechanism: on Stop/SubagentStop, fingerprint HEAD + hashes of changed watched paths; run single `gate.command`
  only if fingerprint changed; feed failure tail back. Does NOT map paths to different checks.
- Overlap: LOW (change-gating only). Opp: shows the Stop-hook integration point RepoMarshal should target.

### Other "agent harness" hooks (low overlap, context only)
- austinm911/agentlint (Claude/Codex Stop hook + required CI check) - https://github.com/austinm911/agentlint
- mauhpr/agentlint (77 rules / 8 packs, real-time guardrails) - https://github.com/mauhpr/agentlint
- mickzijdel/dev-hooks, ChrisWiles/claude-code-showcase (hook examples: lint after edit, tests before stop).
- Stats for these UNVERIFIED.

### Commercial / industrial risk-tiered review (not OSS, but direct conceptual overlap)
- MergeShield (https://www.g2.com/sellers/mergeshield): scores PRs from AI agents (Copilot, Cursor, Claude Code,
  Devin) on ~6 dimensions (security, complexity, blast radius, test coverage, breaking changes); auto-merges
  low-risk from trusted agents. Vendor claims only; methodology UNVERIFIED.
- Vercel monorepo classifier (https://vercel.com/blog/58-percent-of-prs-in-our-largest-monorepo-merge-without-human-review):
  LLM labels PRs LOW/HIGH; hard rules bypass LLM (100+ files -> HIGH; CODEOWNERS paths -> human). ~$0.054/assessment.
- "Merge Mommy" (Claire Vo, built on Vercel Eve): 6 risk dimensions incl. "verification gap"; gray check + Slack.
- Meta RADAR (arXiv 2605.30208): eligibility -> content checks -> LLM review; only low-risk auto-approved.
- GitLab work item 21610: proposed MR risk classification + tiered routing for Duo Code Review incl. weighting
  AI-authored MRs by revert/incident history (https://gitlab.com/groups/gitlab-org/-/work_items/21610).
- CloudBees Smart Tests (ex-Launchable): GA 2026-04-02, explicitly marketed as "control for AI-generated code";
  GenAI semantic test selection per change. Proprietary.
- Design-pattern write-ups: agentpatterns.ai tiered code review; DEV "AI code review and agent verification are
  not the same" (unverified requirement stays unverified, not a pass); aicompetence.org layered assurance model.
- Overlap: these validate the *problem*; all are post-hoc PR review/merge routing, LLM-based, not a
  deterministic pre-work plan with explained skips.

---

## 1. Change-aware / affected-target selection (mechanism prior art)

| Tool | Diff -> work mechanism | Agent-aware? | License | Notes |
|---|---|---|---|---|
| Nx affected (nrwl/nx) | git base..head -> changed files -> project graph -> affected projects + dependents -> run targets | Yes: official Nx agent skill `nx-run-tasks` (~2.5k installs per tessl/skills mirrors) and Nx MCP server | MIT | Very active. Opp: adapter that asks `nx show projects --affected`. |
| Turborepo `--filter=...[ref]` / `--affected` | changed packages + dependents in task graph | Yes: publishes a SKILL.md (per secondary blog; UNVERIFIED) | MIT | Active (Vercel). |
| bazel-diff (Tinder) | hash all targets at two revs, diff hashes -> impacted targets | No | BSD-3-Clause (UNVERIFIED) | BCR 26.0.1, Homebrew lists 39.0.0 (conflicting numbering) - active 2026. |
| Pants `--changed-since` / `--changed-dependents=transitive` | file -> owning targets -> dependents via dependency inference | No | Apache-2.0 | Active (UNVERIFIED 2026 release). |
| CloudBees Smart Tests (Launchable) | ML/GenAI predictive selection per change | Marketed for AI code | Proprietary | GA Apr 2026. |
| Datadog Test Impact Analysis | per-test coverage map -> skip tests not touching changed files | No | Proprietary | Docs current. |
| pytest-testmon | coverage.py fingerprints per test -> rerun affected tests | No | MIT (UNVERIFIED; historically AGPL - check) | 2026 activity UNVERIFIED. |
| Jest `--changedSince` / `--findRelatedTests` | module dependency graph from changed files -> related tests | No | MIT | Ubiquitous. |
| CODEOWNERS + GitHub `paths` filters, dorny/paths-filter, tj-actions/changed-files | glob -> owners / job triggers | No | MIT | Note tj-actions/changed-files supply-chain compromise (Mar 2025) - pin by SHA. |
| danger.js | arbitrary JS rules over PR diff metadata -> warn/fail/message | No | MIT | Closest "explain per-PR rules" UX. |
| Test Impact Analysis MCP (WayneLiu519888) | glob file->test rules + optional call-chain BFS via codebase-memory-mcp -> minimal test set | Yes (MCP, Claude Code) | UNVERIFIED | https://glama.ai/mcp/servers/WayneLiu519888/Test-Impact-Analysis-MCP ; adoption UNVERIFIED. |
| ast-impact-mapper-mcp (vola-trebla) | TS AST -> affected tests | Yes (MCP) | MIT | TS only, small. |
| Parasoft TIA | coverage-based affected tests | No | Proprietary | |

Takeaway: all select *tests/targets* only; none select heterogeneous gates (secrets, dep-audit, UI, runtime) or
explain skips. RepoMarshal should delegate test-level selection to these via adapters rather than re-implement.

---

## 2. AI code review / governance for agent PRs

- CodeRabbit - pre-merge checks (Pro, preview; 4 built-in + up to 5 custom natural-language checks, e.g.
  "API docs updated when schema changes"); won't approve while a check fails; post-merge actions.
  https://docs.coderabbit.ai/pr-reviews/pre-merge-checks . Proprietary. Overlap: MEDIUM ("if path X changes require
  Y" in NL, LLM-evaluated).
- Qodo PR-Agent - OSS (AGPL-3.0, UNVERIFIED current), 2026 activity UNVERIFIED (search returned nothing).
- Graphite Agent (Diamond deprecated, merged into Graphite Agent, Oct 2025) - proprietary.
- GitHub Copilot code review - agentic architecture since 2026-03-05; Comment-only, never blocks.
  https://github.blog/changelog/2026-03-05-copilot-code-review-now-runs-on-an-agentic-architecture/
- Codacy Guardrails - IDE extension + MCP server scanning AI-generated code in real time (SAST, secrets, deps);
  2026 adds public Skills catalog driving Codacy CLIs from Claude/Codex/Copilot. https://www.codacy.com/guardrails .
  Opp: a gate provider RepoMarshal can plan for.
- Sourcery, Semgrep Assistant - not re-verified this pass (UNVERIFIED 2026 status).
- Overlap overall: these are reviewers/gate *executors*, not planners. Opp: treat as optional gate backends.

---

## 3. Context engineering / repo memory

- codebase-memory-mcp (DeusData) - local knowledge graph / call-path tracing MCP. Star counts conflict across
  aggregators (2.1k-44k); UNVERIFIED. https://github.com/DeusData/codebase-memory-mcp
- Serena (oraios/serena) - LSP-backed symbol tools via MCP. MIT (UNVERIFIED). Stats UNVERIFIED.
- Repomix - pack repo/slices into one file. MIT. Stats UNVERIFIED.
- Context7 (upstash/context7) - version-specific library docs MCP/CLI; ~58.7k stars, 2.8k forks, MIT (aggregator,
  updated 2026-07-07). Also in Claude plugin marketplace.
- Claude Context (Zilliz) - vector semantic search MCP. UNVERIFIED stats.
- Aider repo map - tree-sitter + PageRank ranked symbol map (Apache-2.0). Not re-verified.
- RepoMemory (DanielGuru/repomemory) - persistent decision/ADR notes for agents; ~2 stars, updated 2026-04-15.
- Overlap: LOW-MEDIUM. RepoMarshal's "context files" selection is a deterministic subset of what these do.
  Opp: RepoMarshal's plan can recommend these tools/queries rather than build retrieval itself.

---

## 4. Skill registries / routing / scanning

- Agent Skills spec - agentskills.io, published by Anthropic 2025-12-18; ~40 adopters incl. Claude Code, Cursor,
  Codex, Gemini CLI, Copilot, VS Code. RepoMarshal's skill selection should consume SKILL.md frontmatter.
- Registries: skills.sh (Vercel; integrates scanners), Tessl registry (mirrors e.g. nrwl `nx-run-tasks`),
  SkillsMP (~1.9M scraped skills, per agentman.ai), skills.cat, Skillselion (MCP that loads ranked skills
  mid-task = a skill *router*; adoption UNVERIFIED).
- Scanners: Snyk Agent Scan (formerly Invariant mcp-scan; mcp-scan now a redirect pkg; ~3k stars, last push
  2026-09-23 per third party, Apache-2.0 UNVERIFIED); Cisco AI Defense skill-scanner (YAML/YARA + dataflow);
  superagent-ai skill-security; getsentry skill-scanner; openclaw skill-scan / clawscan.
- CSA research note 2026-06-10 (https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-agent-skill-scanner-bypass-20260610-csa/):
  Trail of Bits bypassed all tested skill scanners (Cisco, ClawHub, skills.sh-integrated). Implication: RepoMarshal
  should treat a scanner pass as evidence, not proof (fits UNKNOWN semantics).

---

## 5. MCP security / permission gateways, policy engines

- Preloop (see 6) - MCP firewall + model gateway + YAML/CEL policy + human approvals + session timeline;
  Apache-2.0, 77 stars. Overlap: MEDIUM (policy-as-code for agents); RepoMarshal is upstream planner, Preloop is runtime enforcer.
- Docker MCP Gateway - OSS proxy for MCP servers (credentials, access control). https://docs.docker.com/ai/mcp-gateway/
- ToolHive (Stacklok) - Apache-2.0, Go, container-isolated MCP servers, gateway+registry+runtime.
- agentgateway (Solo.io / LF) - Apache-2.0, Rust, OAuth2.1, CEL policy.
- Invariant Gateway / Guardrails (Snyk Labs) - LLM-call proxy with runtime policy. Snyk acquired Invariant 2025-06-24.
- Others listed in 2026 comparisons: Bifrost, TrueFoundry, Composio, Lunar MCPX (commercial).
- Overlap: LOW with planner; Opp: RepoMarshal "runtime"/"MCP permissions" gate could check gateway config.

---

## 7. Name collision check

Registries (HTTP checks run 2026-10-13):
- PyPI `repomarshal`, `repo-marshal`: 404 (free).
- npm `repomarshal`, `repo-marshal`, `@repomarshal/cli`: 404 (free; scope ownership not checked).
- crates.io `repomarshal`, `repo-marshal`: 404 (free).
- GitHub repo search "repomarshal": 0 results. "repo-marshal": only MHHamdan/repo-marshal (the project itself)
  plus unrelated 0-star personal repos (Marshal-repo, repo_marshall, golf-club "Marshal-Report", etc.).
- Web search for "repomarshal"/"repo marshal": no product, company, or tool found.
- Trademark: no "RepoMarshal" or software "Marshal" AI-code mark surfaced via web search (closest: MARSHALLSOFT,
  1997, comms dev tools). USPTO/EUIPO not queried directly -> UNVERIFIED.

Nearby "marshal" collisions (relevant if a short `marshal` CLI/import name is considered):
- **chiruu12/marshal** - MIT, 3 stars, 472 commits - "orchestration engine driving a fleet of headless coding
  agents (Cursor, OpenCode, Codex, Claude Code) via MCP + Skills"; PyPI `marshal-agents` v0.6.0 (renamed from
  `MarshalFleet`), import `marshal_engine`, **CLI command `marshal`**. Same domain (AI coding agents) -> do not
  ship a bare `marshal` binary; use `repomarshal` (and maybe `rmx`/`repo-marshal`).
- crates.io `marshal` - reserved 2026-04-24 by matutetandil ("A Git workspace manager: monorepo ergonomics over
  multi-repo storage"), v0.0.0-reserved, MIT, https://github.com/matutetandil/marshal . Git/repo tooling adjacent.
- npm `marshal` - Ruby Marshal parser (stale, 2022).
- Python stdlib module `marshal` and Ruby core `Marshal` - a Python import name `marshal` would shadow the stdlib;
  import package must be `repomarshal`.
- Generic CS term "marshalling" - hurts searchability of "marshal" alone, not of "RepoMarshal".

Verdict: "RepoMarshal" / "repo-marshal" / "repomarshal" is CLEAR on PyPI, npm, crates.io, GitHub and the open
web. Avoid the bare "marshal" short name (CLI, import, or package) because of chiruu12/marshal (`marshal` CLI,
same AI-agent space), the crates.io reservation, and the Python stdlib module. Formal trademark search UNVERIFIED.
