# Competitor prior-art: verification / evidence / enforcement tools

Read-only inspection of local clones under `<research-clones>/` (directory names use a single
underscore, e.g. `GenRamzi_AgentProof`; the brief used `__`). No upstream repo was modified and
no code was copied. GitHub web/API was not reachable, so issues, PRs, stars, releases pages,
and download counts were NOT inspected (UNKNOWN for all four).

Labels: **VERIFIED** (cites path:line in the clone), **INFERRED** (reasoned from code/docs but
not executed), **UNKNOWN**.

RepoMarshal hypothesis under test: a local-first, change-aware engineering planner. It takes
task + git diff + repo metadata + policy and deterministically compiles a minimum-sufficient
plan: context files, skills, and gates (lint, typecheck, unit, integration, security, secrets,
dependency audit, runtime, UI), with a reason for every selected or skipped gate. It then records
evidence and evaluates completion as PASS / FAIL / UNKNOWN / BLOCKED / WAIVED.

---

## Summary matrix

| Capability | AgentProof | AgentSmith | DoneProof | AgentWard |
|---|---|---|---|---|
| Reads git diff / changed paths | Yes, BASE..HEAD (VERIFIED) | Only in the autonomous-run controller, for scope enforcement (VERIFIED) | `evidence git-diff` summary only (VERIFIED) | No |
| Selects gates from the diff | No. One fixed test command; proof tests only when new test files appear (VERIFIED) | No. Runs all phases, or a manual `--only` label substring (VERIFIED) | No | No |
| Repo-level gate inventory with reasons | No | **Yes**: `verify discover` gives 8 gate categories with reason, confidence, and coverage state including `not-applicable` (VERIFIED) | No | No |
| Context file / skill selection | No | Profiles chosen from repo stack. One UI-path hook injects a DESIGN.md reminder (VERIFIED) | No | No |
| Risk / domain classification of a change | No (integrity rule families only) | Prose only ("code touching auth...") (VERIFIED) | Free-text `risks[]` | Tool risk ratings, not code changes |
| Executes checks itself | Yes, in BASE and HEAD worktrees | Yes, runs `verify.conf` phases | No (self-reported) | No (runtime proxy) |
| Evidence receipt | `agentproof.receipt/v1` with SHA-256 digest and optional Ed25519 | `receipt.json` with schema_version 1, per-phase stdout/stderr hashes | Receipt schema `urn:doneproof:schema:receipt:1.0` | Audit JSONL with optional HMAC chain |
| Verdict vocabulary | VERIFIED / INCONCLUSIVE / BLOCKED | passed / failed / error (receipt) | awaiting_review / blocked / failed | ALLOW / BLOCK / APPROVE / LOG / REDACT |
| Waiver concept | Policy action `ignore`/`warning` per rule | "deferred: reason" in a prose checklist | `skipped` + `reason` per command | `--dry-run` / `log` |
| License | Apache-2.0 | MIT | MIT | BUSL-1.1 (to Apache-2.0 on 2028-04-24) |

---

## 1. GenRamzi/AgentProof

- **Identifier:** github.com/GenRamzi/AgentProof. Local: `upstream/GenRamzi_AgentProof`
- **SHA:** `7a748e9820bc15e4182aee92c16be5160e2be7e4`
- **Last commit:** 2026-08-15 11:43:17 +0000 ("Support SBOM generation on Python 3.10")
- **Activity:** 19 commits. First commit 2026-08-15 00:38 +0300 (= 08-14 21:38 UTC), so the whole
  history covers about 14 hours. Tags `v0.2.0rc2` and `v0.2.0rc1`. pyproject version
  `0.2.0rc3.dev0`. Burst-built and young (VERIFIED from `git log`). README says the PyPI name
  `agentproof` belongs to an unrelated project, which blocks a stable release (README.md:188-190).
- **License:** Apache-2.0. `LICENSE` holds the canonical Apache 2.0 text. `pyproject.toml`
  sets `license = "Apache-2.0"`. No NOTICE file (VERIFIED).
- **Deps:** `PyYAML>=6.0`, `jsonschema>=4.0`. Optional `signing = cryptography>=42`. Python 3.10 or later.
- **Layout:** `agentproof/{adapters,checks/{ci,contracts,coverage,dependencies,tests},engine,policy,proof,receipt,reporters,schema_data}`.
  About 1,934 LOC. `schemas/{receipt,claim,policy}-v1.schema.json`. 20 BASE/HEAD fixture pairs
  under `fixtures/`. `action.yml` is a composite GitHub Action. `services/github-app/` is a README only.
- **CLI** (VERIFIED, agentproof/cli.py:118-153): `init [--path]`; `verify|proof|audit` take
  `--repo --base --head --test-command --setup-command --proof-test(*) --claim(*) --policy
  --policy-preset {default,strict,enterprise} --timeout --json --markdown --sarif --no-auto-proof`;
  `receipt verify <path>`; `explain <RULE>`; `doctor [--repo]`. Exit code 0 only on VERIFIED
  (cli.py:71).
- **Tests:** 71 `def test` across unit, adversarial, integration, e2e, and fixtures (VERIFIED by
  grep count). They cover integrity rules, parser fuzzing, receipt integrity, setup phase,
  schema/action sync, and version discipline.

### BASE vs HEAD mechanism (VERIFIED)
- If refs are not given, `discover_refs` uses `merge-base` against origin/main, main,
  origin/master, or master, falling back to `HEAD^` (cli.py:24-31).
- `verify_core` computes `changed_files` and `diff_by_file` (engine/verifier.py:79-80). It runs
  diff-text integrity detectors (verifier.py:86-94), creates two clean git worktrees
  (verifier.py:101-103), fingerprints the environment of each, and runs the optional setup command
  and then the **single canonical test command** in both (verifier.py:116-138). AST assertion
  comparison and JSON-contract diffs follow (verifier.py:139-143).
- **Proof tests:** an explicit `--proof-test` is run on BASE and HEAD. New test files in the diff
  matching `(tests?|spec|__tests__)/...test...\.(py|js|ts…)` are auto-discovered
  (proof/tests.py:73-74) and "transplanted" into BASE. Classification
  (proof/tests.py:36-55): BASE fail and HEAD pass = PROVEN; both pass = INCONCLUSIVE; both fail
  = NOT_FIXED; BASE pass and HEAD fail = REGRESSION; plus UNREPRODUCIBLE and
  ENVIRONMENT_MISMATCH.
- **Verdict** (verifier.py:174-181): INCONCLUSIVE if setup failed. BLOCKED if the HEAD tests
  fail, a policy-blocking finding exists, a proof is blocked, or a proof is required but none
  ran. INCONCLUSIVE if any proof is inconclusive. Otherwise VERIFIED.

### Anti-test-manipulation (VERIFIED, rules.py:21-44)
These are regex and AST heuristics over the diff:
- AP001 deleted tests: removed `def test_` / `it(` / `test(` / `@Test` lines in test-ish paths
  (checks/tests/discovery.py:8-16).
- AP002 new skips. AP003 focused tests (`.only`).
- AP004 discovery reduced: added `--ignore`, `testpaths`, `paths-ignore`, `testMatch`, etc. in
  config files (discovery.py:19-28).
- AP005 assertion weakened (Python AST). AP006 coverage exclusion. AP007 snapshot overwrite.
  AP008 mock weakening.
- CI: AP101 job removed, AP102 trigger/path filter narrowed, AP103 test command reduced
  (heuristic, including a `len(after) < len(before)` fallback at discovery.py:48), AP104 CI
  changed.
- Supply chain: AP301 dependency expansion, AP302 lockfile drift, AP401 JSON contract changed,
  AP501 receipt invalid, AP502 untrusted environment.

### Policy format (VERIFIED, policy/evaluator.py:12-33)
YAML with `version: 1` (anything else is rejected, evaluator.py:79). Unknown keys are rejected
per section. The allowed top-level keys are `verification` (test_command, setup_command,
require_tests, proof_tests: off|auto|required), `integrity` (per-rule action: ignore |
warning | review | block), `coverage`, `dependencies`, `network` (mode deny|install-only|allow,
recorded but not enforced), `receipt` (signature_required, trusted_public_keys), and `security`
(isolated_runner_required). There are three presets.
- `coverage.minimum_delta` is accepted but no code consumes it (grep finds it only in
  evaluator.py:16,30). INFERRED: this field is not implemented.
- `proof/mutations.py` and `proof/behavior.py` are stubs that nothing in the verifier calls
  (INFERRED from imports in verifier.py:13-34).

### Receipt format (VERIFIED)
- `schema: "agentproof.receipt/v1"` (models.py:95).
- Fields: `subject{repository,base_sha,head_sha}`, `verdict`, `claims`, `proof_tests`,
  `test_runs{base,head}`, `setup_runs`, `integrity_findings`, `environment{base,head,comparison}`,
  `policy`, `evidence_graph{nodes,edges}`, `digest` ("sha256:" over canonical JSON), and
  `signature` (optional Ed25519). See schemas/receipt-v1.schema.json and docs/receipts.md.
- Claim statuses: PROVEN / SUPPORTED / UNPROVEN / CONTRADICTED / NOT_APPLICABLE
  (engine/verdicts.py:3).

### Change-aware gate selection?
**No (VERIFIED).** There is exactly one test command (from the policy, the CLI, or
auto-discovery), and it runs in full on both BASE and HEAD. The diff only drives (a) which
integrity detectors fire and (b) whether proof tests are required
(`proof_required`, verifier.py:148). There is no lint, typecheck, secrets, dependency-audit, or
UI gate execution. There is no "gate skipped because..." reasoning, no context or skill
selection, and no task input other than free-text `--claim` strings, which are keyword-matched
(verifier.py:55-72).

### What it intentionally does not do (VERIFIED)
It is "not a coding agent, an AI code reviewer, or a security scanner" (README.md:7). It is not a
sandbox, and network mode is a declaration rather than a firewall (docs/threat-model.md:21). It
does not use `pull_request_target`. Cloud, billing, dashboards, and the privileged GitHub App are
deferred (ROADMAP.md:5).

### Overlap with RepoMarshal
- **High** on the "verify completion with evidence" half: BASE/HEAD evidence, verdicts, a stable
  receipt id, policy actions per finding, and SARIF.
- **None** on planning: no gate selection, no reasons for skipped gates, no context or skill
  plan.
- Its verdict set (VERIFIED/INCONCLUSIVE/BLOCKED) partly overlaps PASS/UNKNOWN/BLOCKED. It has
  no WAIVED; `ignore`/`warning` actions are the nearest equivalent.

### Integration opportunity
RepoMarshal could select AgentProof as a gate ("test-integrity / proof" gate) when the diff
touches tests, CI, or lockfiles. It could pass the planned test command as `--test-command` and
ingest `agentproof-receipt.json` as evidence. The schema has a stable id, and the Apache-2.0
license is compatible. The anti-tamper rule IDs (AP001-AP104) are a good reference taxonomy for
RepoMarshal's own "suspicious change" signals. Reference them; do not copy them.

**Strengths:** genuine differential (BASE vs HEAD) execution; proof-test semantics; stable rule
IDs; honest threat model.
**Weaknesses:** single test command; regex heuristics; a 14-hour-old codebase in RC with an
identity/PyPI blocker; stubbed coverage/mutation features; Python/JS-centric.

---

## 2. PromptPartner/agentsmith (closest overlap)

- **Identifier:** github.com/PromptPartner/agentsmith. Local: `upstream/PromptPartner_agentsmith`
- **SHA:** `0901316e730c4aca7a2aabe1562c4d63b1ea0444`
- **Last commit:** 2026-10-12 00:58:24 +0200 (merge of PR #44, a Windows stop-signal fix)
- **Activity:** 217 commits from 2026-07-16 to 2026-10-12 (Jul 20, Aug 77, Sep 93, Oct 27).
  Tags run v0.2.3 to v0.3.1; the latest tag is v0.3.1 (2026-09-04). PR numbers reach #44. This is
  the **most active** of the four (VERIFIED from `git log`).
- **License:** MIT, "Copyright (c) 2026 PromptPartner" (LICENSE:1-3). No NOTICE file. There is no
  pyproject. The tool is a single-file Python CLI (`agentsmith.py`, 8,121 LOC) plus a PyInstaller
  spec for standalone builds. Bundled site fonts are SIL OFL according to DESIGN.md.
- **Layout:** `core/` (7 rule files), `profiles/` (10 work profiles), `skills/` (verify,
  handoff, wayfinder, grill-with-docs, autonomous-run, ...), `hooks/` (Claude/Codex
  PreToolUse etc.), `config/` (agents registry, verify presets, MCP example), `templates/`,
  `scripts/` (verify.sh, autonomous-run.py at 2,098 LOC, secret-scan, leak-gate),
  `work_graph.py`, `qualification.py`, `project_memory.py`, `config_audit.py`, and 27 docs.
  Five READMEs (en/de/es/fr/zh-CN).
- **CLI** (VERIFIED, agentsmith.py:7694-7887): `install` (many flags, including `--profile`,
  `--agent`, `--with-skills/mcp/hooks`, `--dry-run`, `--uninstall`), `update
  {check,plan,apply,rollback,configure}`, `agents list`, `doctor`, `status`, `profiles
  {list,recommend,switch}`, `compatibility`, `verify [discover|apply] [--only --list --record DIR
  --tree-class {clean-clone,disposable-fixture,linked-worktree,operator-worktree} --json --save
  --plan --dry-run]`, `validate-integration --checkpoint`, `graph ...`, `demo first-loop`,
  `resume`, scaffold commands, `secret-scan`, `audit-config [--fail-on]`, `memory ...`, `hook
  <name>`, and `evaluate --agent {claude,codex,native}`.
- **Tests:** 39 `scripts/test-*` scripts with about 421 Python `def test_` plus bash suites. They
  are wired as about 40 phases in its own `.harness/verify.conf`. CI is
  `.github/workflows/{verify,standalone}.yml`. There are compatibility evaluation records under
  `compatibility/evaluations/` for Claude and Codex.
- **Agent compatibility** (VERIFIED, config/agents.json): 16 agents. Claude Code and Codex are
  `native`. Cursor, Copilot, Gemini CLI, Windsurf, Cline, Roo, Aider, Continue, OpenHands, Goose,
  OpenCode, Junie, Zed, and Jules are certification targets with `certification: pending`.
  Support tiers are native / certified / community-compatible.

### Rules, profiles, definition of done (VERIFIED)
- Core rules R1-R8 are in `core/20-principle-rules.md`. **R5:** "Run the full check for this
  work type — **not just the one thing you touched**" (core/20-principle-rules.md:35-37).
- Each profile has a prose quality-gate checklist. For example, software-dev
  (profiles/software-dev.md:55-79) lists build, typecheck, lint, a test that failed first, the
  full suite, a real-path run, a UI/DESIGN.md check ("inert if this project has no UI"), "code
  touching auth, user input, or secrets got a named security pass", CVE audit, docs, and defect
  ticket. The rule says **"deferred: reason" is allowed, silence is not** (line 56), which is a
  waiver-with-reason concept in prose.
- Verify presets: `config/verify-presets/*.conf` contain commented examples per profile
  (build/types/lint/test/deps/secrets).

### Verification and evidence (VERIFIED)
- `.harness/verify.conf` has one phase per line, `label :: shell command`. Both
  `scripts/verify.sh` and `agentsmith verify` run **every phase in order and stop at the first
  failure**. The only selection is `--only <tag>`, a **manual substring match on the label**
  (scripts/verify.sh:67; agentsmith.py:6635:
  `phases = [... if not args.only or args.only in label]`).
- `verify --record DIR --tree-class X` writes `receipt.json` (agentsmith.py:6667-6758). It holds
  `schema_version:1`, `status` (running|passed|failed|error), `started_at/updated_at/completed_at`,
  `target`, `tree_class`, `only`, `configuration{path,sha256}`, `git` metadata,
  `platform{os,python_version}`, and `phases[]` with `{index,label,command,status,started_at,
  completed_at,duration_seconds,exit_code,output_paths{stdout,stderr},output_hashes,redactions}`.
  Secrets are redacted. There is no `skipped`, `unknown`, or `waived` state.
- **`verify discover`** builds a "verification discovery plan v1"
  (`$id https://agentsmith.dev/schemas/verification-plan-v1.json`,
  tests/fixtures/first-verified-loop/v1/schemas/verification-plan.schema.json). Fields:
  `input_fingerprint` (sha256 over the inspected files), `detected_stacks`
  (python/node/go/rust/shell/documentation), `evidence[]` (manifest/source/test/configuration/
  ci-hint/task-runner-hint), `existing_phases`, `proposed_phases[]` with
  **`category, label, command, detector, source_path, reason, confidence_class, requires_review`**,
  and `coverage` over **8 fixed categories**: `build, type_check, lint_static_analysis,
  unit_integration_tests, dependency_risk, secret_scan, real_path_exercise, judgment_evaluation`.
  Each category has `state ∈ {configured, recommended, manual, missing, not-applicable}`, plus
  `evidence[]` and a `gap` string. `unresolved[]` and `warnings[]` complete the plan.
  `verify apply --plan` writes a reviewed plan into verify.conf.
- **This is the closest prior art to RepoMarshal's gate plan.** It has per-gate reasons, a
  not-applicable state, deterministic fingerprinting, and a plan/apply split. **But its input is
  the repository tree only.** `build_verification_plan(target: Path)` takes no diff, base, task,
  or changed-path argument (agentsmith.py:5690-5694). It answers "what gates does this repo
  have/need?", not "what gates does this change need?". The plan configures the repo once. It is
  not compiled per change.

### Diff / path-based mechanisms (VERIFIED). None of them select gates.
1. **Autonomous run controller** (scripts/autonomous-run.py). A maker/checker loop has an
   operator manifest (templates/autonomous-run.json) with `scope.allowed_paths`,
   `scope.denied_paths` (globs), `verify.command`, `verify.protected_paths`, and limits.
   `changed_paths()` = `git diff --name-only --no-renames base..head` (autonomous-run.py:800-803).
   `validate_verification_changes` **rejects** any change to `VERIFICATION_POLICY_PATHS`
   (.agentsmith/**, .harness/**, .github/workflows/**, scripts/verify*, ...) or edits/deletions
   of baseline test files (`BASELINE_TEST_PATHS`) (autonomous-run.py:806-834). This is
   path-based scope **enforcement** and anti-tamper, comparable to AgentProof AP001/AP101. The
   verify command stays fixed whatever the diff.
2. **UI hook** (hooks/ui-design-reminder.sh:4-6). This PreToolUse nudge fires once per session
   when an Edit/Write/apply_patch targets `*.tsx|*.jsx|*.vue|*.svelte|*.css|...|*/components/*|
   */ui/*` and DESIGN.md exists. It injects one line of context. This is the **only** automatic
   path-to-context mapping found, and it is hard-coded.
3. **Profile recommendation** (agentsmith.py:5207-5274) maps detected stacks to profiles
   (python/node/go/rust to software-dev, shell to devops-setup, docs to document-creation). It is
   based on the repo, not the change, and is applied at install or switch time.
4. **Risk/domain classification:** only prose in profiles ("code touching auth...",
   software-dev.md:67), which the agent must interpret. There is no deterministic
   classifier (INFERRED from a grep of core/ and profiles/ plus the absence of any classifier in
   agentsmith.py).

### Handoffs and persistent state (VERIFIED)
`skills/handoff/SKILL.md` and `scripts/handoff.sh` write `.harness/handoffs/handoff-*.md`.
`agentsmith resume` validates a handoff. `.agentsmith/state.json` records owned settings.
Project memory search, work graphs (`work_graph.py`; schema nodes `{run_id, manifest_path,
manifest_sha256, depends_on}`), and durable checkpoints are also present.

### What fundamentally distinguishes a diff-driven planner from AgentSmith
- AgentSmith's model is **"configure the full check once, then always run all of it"**. R5
  explicitly rejects "just the one thing you touched". RepoMarshal's model is **"compile a
  per-change, minimum-sufficient plan"**. That is a philosophical conflict, not only a missing
  feature. RepoMarshal must justify minimality (cost/latency on large repos, monorepos) while
  preserving safety, for example by escalating to the full suite when the diff touches
  build/CI/shared config.
- AgentSmith's adaptive judgement ("security pass if touching auth", "UI check if UI") lives in
  **prose the LLM interprets**. RepoMarshal proposes a **deterministic, inspectable mapping**
  diff → domains → gates with a machine-readable reason per selected or skipped gate. It also
  adds context-file and skill selection per change, which AgentSmith does not do.
- AgentSmith's receipt has no skipped/waived/unknown gate states and no link from a gate to the
  change that required it.
- AgentSmith is a whole harness: installer, rules, hooks, memory, autonomous loops, 16-agent
  registry. RepoMarshal could be a narrow engine that AgentSmith-style harnesses call.

### Overlap with RepoMarshal
- **High:** definitions of done, gate categories (its 8 categories nearly match RepoMarshal's gate
  list, with no separate UI/runtime split; `real_path_exercise` ≈ runtime), reasons per
  proposed gate, not-applicable state, evidence receipts with output hashes, handoff/resume,
  local-first, Claude/Codex support.
- **Absent:** diff/task input to planning, per-change gate selection, skipped-gate reasons
  derived from the change, context/skill selection per change, a 5-state completion verdict,
  path-based gate rules.

### Integration opportunity
RepoMarshal could (a) read `.harness/verify.conf` labels as the repository's gate inventory, or
(b) consume an AgentSmith verification-plan v1 JSON as "repo metadata". It could then emit
`agentsmith verify --only <label>` invocations (substring-only, so labels would need to be
distinct), or a temporary filtered verify.conf, and ingest `receipt.json`. The license is MIT.
The schemas live only as test fixtures, not as published stable artifacts (INFERRED: stability
risk).

**Strengths:** mature, very active, heavily tested, multi-agent, honest about limits
(KNOWN-ISSUES.md), strong anti-tamper in autonomous mode, the repo-level gate coverage model.
**Weaknesses:** per-change adaptivity depends on the LLM reading prose; all-or-substring
execution; 8k-line single-file CLI; scope is very broad (installer, harness, memory, loops).

---

## 3. giugiu-a11y/doneproof

- **Identifier:** github.com/giugiu-a11y/doneproof. Local: `upstream/giugiu-a11y_doneproof`
- **SHA:** `4a45ebc01fb41c33130657e8addd15f4c9bcc4d4`
- **Last commit:** 2026-08-23 01:37:33 -0300 ("Make DoneProof easier to trust, share, and
  contribute to (#38)")
- **Activity:** 9 commits (squash-merged PRs up to #38). v0.4.0 on 2026-04-21 and v0.5.0 on
  2026-07-14. Since then there have been only docs/workflow commits. Activity is **low**, mostly
  documentation and launch material (VERIFIED from `git log`).
- **License:** MIT, "Copyright (c) 2026 DoneProof contributors". pyproject sets `license = "MIT"`
  and `license-files=["LICENSE"]`. No NOTICE file.
- **Deps:** `jsonschema>=4.25.0`, `PyYAML>=6.0`. Python 3.10 or later. About 1,405 LOC in
  `src/doneproof/`.
- **CLI** (VERIFIED, src/doneproof/cli.py:24-180): `init`, `new --task --summary --status
  --changed-file(*) --command "status:cmd"(*) --evidence "type:value"(*) --risk(*)`, `check
  [--root --receipt --json]`, `schema-check`, `report [--format json]`, `badge [--format
  markdown|json]`, `evidence git-diff [--mode staged|...]`, `doctor`. The composite GitHub Action
  runs only `doneproof check` (action.yml:14-22).
- **Tests:** 50 `def test` in 4 files (cli 16, git 7, project_files 16, receipt 11).
- **Agent templates:** aider, claude, cline, codex, cursor, hermes, openclaw, opencode
  (.doneproof/templates/).

### Receipt (VERIFIED, schemas/receipt.schema.json, `$id urn:doneproof:schema:receipt:1.0`)
- Required: `task, status, summary, changed_files[≥1], commands[≥1]{cmd, status ∈
  passed|failed|skipped, reason?}, evidence[≥1]{type,value}, risks[] (strings)`.
- Optional: `schema_version` (string, "1.0" in policy) and `created_at`.
  `additionalProperties: true`.
- **Remaining risks** are free-text `risks[]`, required but allowed to be empty.
- **Human-review status:** there is no reviewer field. Instead `status` is restricted to
  `awaiting_review | blocked | failed`. `done/complete/validated/100%/pronto` are forbidden, and
  forbidden claim phrases are scanned in text (.doneproof/policy.yml; receipt.py:246-280). A
  human-approved state is deliberately absent.
- `skipped` without a `reason` is only a *warning* (receipt.py:182-183).
- `changed_files` are only checked as safe repo-relative paths that exist locally
  (receipt.py:207-243). They are **not** cross-checked against `git diff`, and commands are
  **not executed**. Everything is self-reported by the agent (VERIFIED: no subprocess in
  receipt.py; git.py only builds a numstat summary).

### Overlap with RepoMarshal
Low to moderate. It overlaps on the evidence/receipt idea and on "skipped needs a reason" and
"residual risks". It has no planning, no execution, no verification of claims, and no gate
taxonomy. Its status vocabulary (awaiting_review/blocked/failed) is a useful contrast: RepoMarshal
should keep "machine PASS" separate from "human approved".

### Integration opportunity
RepoMarshal could **emit a DoneProof receipt** from its evidence ledger (task, changed_files from
the diff, commands from executed gates, evidence, risks from UNKNOWN or WAIVED gates). The format
is trivial, MIT, with a stable `$id`. This positions RepoMarshal as a producer of verified
receipts, where DoneProof only checks self-reported ones.

**Strengths:** simple, clear human-review stance, multi-agent templates, digest-pinned Action.
**Weaknesses:** no verification of truthfulness; low activity; tiny code base; the schema is
loose (`additionalProperties: true`).

---

## 4. agentward-ai/agentward

- **Identifier:** github.com/agentward-ai/agentward. Local: `upstream/agentward-ai_agentward`
- **SHA:** `0e049066a8f4b48196803e330bc2f0a54bb2eda4`
- **Last commit:** 2026-04-28 22:34:53 -0500 ("report: spell out 'control gaps'...")
- **Activity:** 118 commits from 2026-02-19 to 2026-04-28 (Feb 26, Mar 72, Apr 20). Tags v0.5.0,
  v0.5.1, and v0.5.2, all on 2026-04-25. There have been **no commits for about 5.5 months** as
  of 2026-10-13. Activity is stalled or slowed (VERIFIED from `git log`; there may be activity on
  other branches or the hosted product, UNKNOWN).
- **License:** **BUSL-1.1** at HEAD (LICENSE: Licensor OpenSafe Inc., Change Date 2028-04-24,
  Change License Apache-2.0, LICENSE:44-46; pyproject `license = "BUSL-1.1"`). LICENSE-CHANGE.md
  says commits before 2026-04-24 remain Apache-2.0. The Additional Use Grant forbids
  hosted or embedded competitive offerings. **Do not embed or vendor its code.** Emitting a
  policy file for a user's own install of AgentWard is just producing data (INFERRED: low
  licence risk, but not legal advice).
- **Deps:** typer[all] plus pydantic, rich, etc. (pyproject.toml:27+). Python 3.11 or later.
  Also published as an npm wrapper (`npm/`) and a Dockerfile.
- **Tests:** about 3,453 `def test` across about 75 test files (VERIFIED by grep count).

### Runtime enforcement mechanism (VERIFIED, README "How It Works" and agentward/proxy/)
- It is a **proxy**, not agent hooks. There are two modes. (1) A **stdio JSON-RPC 2.0 MCP proxy**
  intercepts `tools/call` (`agentward inspect --policy X -- <mcp server cmd>`). (2) An **HTTP
  reverse proxy + WebSocket** for the OpenClaw gateway (`POST /tools-invoke`).
- `agentward setup` rewrites MCP client configs so servers launch behind the proxy.
- Clients discovered: Claude Desktop, Cursor, VS Code, Windsurf, Claude Code `.mcp.json`
  (scan/config.py:175-195), and OpenClaw skills. Python tool definitions
  (OpenAI/LangChain/CrewAI/Anthropic) are scanned statically only.
- **It does not intercept Claude Code or Codex built-in tools (Edit/Write/Bash).** A grep for
  `PreToolUse` or Claude settings hooks finds nothing (VERIFIED). Only MCP-routed tools are
  enforced.
- Further layers: approval (TTY, Telegram), an LLM judge (optional extra LLM call), a session
  evasion detector, a deobfuscation pipeline, a sensitive-content classifier, a non-overridable
  `PROTECTED_DIRS` (~/.ssh, ~/.aws, ...) (policy/protected_paths.py:25-39), a circuit breaker,
  and a behavioural baseline.
- An MCP server exposing `agentward_scan`, `agentward_configure`, `agentward_comply`, and
  `agentward_map` (mcp_server.py:27-99; note the stale `SERVER_VERSION="0.3.2"`).

### Policy language (VERIFIED, agentward/policy/schema.py:1005-1126)
`agentward.yaml` is a pydantic `AgentWardPolicy`. Fields: `version: str` (required; the value is
not validated against a fixed enum, INFERRED from schema.py:1013 and loader.py), `default_action`
(allow|block), `skills: {skill: {resource: ResourcePermissions}}`, `skill_chaining`,
`chaining_mode`, `skill_chain_depth`, `require_approval` (with conditional `when`),
`approval_timeout`, `sensitive_content`, `data_boundaries`, `skill_metadata`, `sequence_rules`,
`llm_judge`, `audit{syslog_path}`, `session`, `capabilities: {tool: {arg: constraints}}`
(must_start_with, allowlist/blocklist globs, allowed_domains, cidrs, min/max, ...),
`deobfuscation`, `registry_check`, `warn_unregistered`, `baseline_check`, and
`baseline_warn_threshold`. Actions are allow / block / approve / log / redact. No `model_config`
`extra="forbid"` was found, so unknown keys are likely silently ignored (INFERRED, pydantic
default). `agentward diff old.yaml new.yaml` classifies policy changes as breaking or relaxing.

### Audit log (VERIFIED, audit/logger.py:120-129; audit/integrity.py)
JSONL `agentward-audit.jsonl` with `{timestamp, event, tool, arguments (PII-redacted), decision,
reason, skill, resource, [chain_violation], [dry_run]}`. Other event types: tool_result,
startup, http_request, approval_dialog, sensitive_data_blocked, boundary_violation,
judge_decision, session events, and more. An RFC 5424 syslog copy is always written. An optional
HMAC chain (`prev_hash`, `hmac`) applies when `AGENTWARD_AUDIT_HMAC_KEY` is set.

### Could RepoMarshal emit a policy for it?
**Partly.** The format is documented, pydantic-validated, and versioned by a free `version`
string. It is reasonably stable but not formally versioned (INFERRED). RepoMarshal could compile
its plan's allowed write scope into `capabilities.write_file.path.allowlist` or `must_start_with`
and `require_approval` entries for an MCP filesystem/shell server. This only constrains
**MCP-routed** tools, not native agent edits, so its value for coding-agent change control is
limited. The project is also stalled and BUSL-licensed.

### Overlap with RepoMarshal
**Low.** AgentWard governs runtime tool-call permissions and compliance for agent *actions*.
RepoMarshal plans and verifies code *changes*. Shared ideas: policy-as-code, a reason per
decision, audit evidence, and dry-run mode.

**Strengths:** real enforcement outside the LLM; rich constraint language; large test suite;
SIEM-ready audit; compliance mapping.
**Weaknesses:** BUSL relicense; dormant since 2026-04-28; no coverage of native coding-agent
tools; mostly irrelevant to diff/gate planning.

---

## What remains uncovered (RepoMarshal's defensible gap)

The evidence covers the four repos above. Other competitors were not assessed here.
1. **Per-change deterministic gate compilation.** No inspected tool maps *diff + task* to a
   *selected subset* of gates with machine-readable reasons for both selected and **skipped**
   gates. AgentSmith does this per repo (verification-plan v1). AgentProof uses the diff only
   for tamper detection and proof-test triggering.
2. **Per-change context-file and skill selection.** Nothing found beyond AgentSmith's single
   hard-coded UI-path nudge.
3. **Deterministic change-domain or risk classification** (auth, secrets, deps, UI, migrations,
   CI). AgentSmith has it only in prose. AgentProof has integrity-rule families. AgentWard
   classifies tools, not changes.
4. **A five-state completion verdict including WAIVED and UNKNOWN**, linked to plan items. The
   closest equivalents are AgentProof (VERIFIED/INCONCLUSIVE/BLOCKED plus `ignore`/`warning`
   actions), DoneProof (`skipped` + reason), and AgentSmith ("deferred: reason" in prose).
5. **A plan → evidence → evaluation loop in one artifact.** Each tool does one leg: AgentSmith
   does inventory and execution, AgentProof differential verification, DoneProof the receipt
   shape, AgentWard runtime enforcement.

## Risks to the hypothesis
- AgentSmith is active (93 commits in September) and already has the plan schema with
  categories, reasons, and not-applicable states. Adding a `--base/--diff` input to
  `verify discover` would be a small step for them. RepoMarshal's moat must therefore be the
  quality of the deterministic diff→domain→gate rules, escalation safety (when to fall back to the
  full suite), and an interoperable plan/evidence schema, not "gate plans" as a concept.
- AgentSmith's R5 ("not just the one thing you touched") is the strongest counter-argument to
  "minimum-sufficient" plans. RepoMarshal must state when selection is safe, for example by
  always running cheap global gates and selecting only expensive ones.
- AgentProof's BASE/HEAD differential and anti-tamper checks are a stronger completion check than
  plain gate execution. RepoMarshal should integrate or reference that kind of check rather than
  rebuild it.
