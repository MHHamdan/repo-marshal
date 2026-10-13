# RepoMarshal — Research, Architecture, and Initial Implementation Mission

You are working on a new open-source developer-tool project named:

# RepoMarshal

Canonical target repository:

**GitHub owner/repository:** `MHHamdan/repo-marshal`

The repository has already been initialized with a first commit and `main` branch.

Your job is to research the current agentic-development ecosystem, identify the strongest unresolved problem, architect RepoMarshal around that problem, and then implement the smallest convincing production-quality vertical slice.

This is not an exercise in generating a lot of code.

This is an attempt to create a serious open-source engineering project with the potential for broad adoption.

---

# 0. Critical Operating Rule

The target repository is:

`MHHamdan/repo-marshal`

Only modify this repository.

All other repositories mentioned below are:

- research sources;
- architectural references;
- interoperability targets;
- competitive prior art;
- possible adapter targets.

Do NOT:

- push to them;
- open automated PRs against them;
- alter them;
- vendor them blindly;
- copy entire directories;
- cherry-pick commits from them;
- transplant source without checking licensing;
- present upstream functionality as RepoMarshal functionality.

You may inspect or clone upstream repositories into temporary/read-only research locations if your environment permits it.

Treat upstream code as third-party intellectual property even when publicly visible.

---

# 1. Product Mission

RepoMarshal should become a:

> **local-first engineering control plane for AI coding agents**

Its purpose is to decide, for each proposed repository change:

1. what project context the agent actually needs;
2. what skills/capabilities the agent should be given;
3. whether those skills/capabilities are safe;
4. what permissions should apply;
5. what tests/checks are necessary;
6. whether runtime verification is necessary;
7. whether frontend/UI/UX checks are necessary;
8. what security checks are necessary;
9. what evidence must exist;
10. whether the work may legitimately be considered complete.

The key idea is not merely:

> "Run lots of tools."

The key idea is:

> **Determine the minimum sufficient engineering process required for THIS change in THIS repository.**

Call this concept, provisionally:

# Change-Aware Engineering

A tiny documentation edit should not run an expensive browser workflow.

A React component modification may require:

- targeted frontend context;
- UI skills;
- accessibility verification;
- browser runtime verification.

An OAuth implementation may require:

- authentication architecture context;
- security-focused skill routing;
- dependency inspection;
- secrets scanning;
- unit tests;
- integration tests;
- runtime verification;
- stronger completion evidence.

RepoMarshal should intelligently marshal these capabilities.

---

# 2. Product Identity

Project name:

**RepoMarshal**

Repository slug:

`repo-marshal`

Likely CLI:

`repomarshal`

Working tagline:

> **Marshal every AI-generated change from context to proof.**

Working description:

> **RepoMarshal determines the context, skills, security checks, runtime verification, quality gates, and evidence required before an AI-generated repository change can be considered complete.**

Do not rename the project casually.

Only recommend renaming if you discover a serious:

- trademark issue;
- major open-source collision;
- package-registry conflict;
- commercial developer-tool conflict;
- confusion risk with a closely related project.

If such a problem exists, document concrete evidence before suggesting alternatives.

---

# 3. Do Not Build a Five-Tool Wrapper

The original inspiration comes from five projects:

1. NVIDIA SkillSpector
2. Reticle
3. Chisel
4. UI Skills
5. UI/UX Pro Max

RepoMarshal must NOT become:

```text
SkillSpector
+ Reticle
+ Chisel
+ UI Skills
+ UI/UX Pro Max
+ shell script
```

That would be weak, fragile, hard to maintain, and insufficiently original.

Instead, study what each project does well and determine:

- what should be integrated;
- what should remain external;
- what capability RepoMarshal itself should provide;
- what should become an adapter;
- what should merely influence architecture;
- what should explicitly NOT be reproduced.

RepoMarshal must have a reason to exist even if all five projects continue improving independently.

---

# 4. Primary Upstream Research Sources

Research these exact canonical GitHub repositories.

Do not use forks unless investigating fork-specific innovation.

---

## 4.1 NVIDIA SkillSpector

Canonical repository identifier:

`NVIDIA/SkillSpector`

Study it for:

- AI agent skill security;
- malicious skill detection;
- prompt injection;
- data exfiltration;
- excessive agency;
- dangerous code;
- MCP risks;
- supply-chain analysis;
- static analysis;
- optional semantic/LLM analysis;
- machine-readable output;
- SARIF;
- risk scoring;
- MCP integration;
- security architecture.

Determine:

- CLI interfaces;
- JSON/SARIF contracts;
- MCP interface;
- whether subprocess integration is sufficient;
- whether an adapter is preferable;
- whether any reusable API is stable;
- current license;
- dependency model.

Do not reinvent its scanner unless RepoMarshal needs a small independent fallback capability.

RepoMarshal should ideally be capable of using SkillSpector as a security provider.

Potential abstraction:

```text
SecurityScanner

├── SkillSpectorAdapter
├── NativeBaselineScanner
└── FutureScannerAdapter
```

---

# 4.2 Reticle

Canonical repository identifier:

`reticlehq/reticle`

Study it for:

- machine-native runtime perception;
- application verification;
- browser/app state inspection;
- network observation;
- console observation;
- application state observation;
- source-location evidence;
- saved verification flows;
- PASS / FAIL / UNKNOWN semantics;
- MCP integration;
- local-first verification.

Pay particular attention to licensing.

Current upstream structure may have different licenses for different components.

Verify the exact current license of every component you might interact with.

Do not treat "repository is public" as permission to copy the server.

Study whether RepoMarshal should:

- integrate Reticle;
- invoke Reticle;
- provide a RuntimeVerifier adapter;
- use Playwright as another provider;
- define a common verification-result contract.

Potential interface:

```text
RuntimeVerifier

├── ReticleAdapter
├── PlaywrightAdapter
├── HTTPVerifier
└── CustomVerifier
```

Preserve three-state semantics where appropriate:

```text
PASS
FAIL
UNKNOWN
```

`UNKNOWN` must never silently become `PASS`.

---

# 4.3 Chisel

Canonical repository identifier:

`chisel-sh/chisel`

Study it for:

- project knowledge;
- specs;
- active-work lifecycle;
- local-first architecture;
- Markdown-first information;
- Git-backed knowledge;
- machine-oriented context;
- context creation;
- structured project state;
- fast CLI design.

IMPORTANT:

The current Chisel license must be examined carefully.

At the time this project was conceived, Chisel was using:

`FSL-1.1-Apache-2.0`

Do not assume this remains unchanged.

Record:

- current license;
- commit inspected;
- conversion terms;
- restrictions on competing products/services;
- what integration forms are legally safe.

Prefer learning from the concept or using documented interoperability instead of transplanting implementation.

Potential abstraction:

```text
ContextProvider

├── RepoMarshalNativeProvider
├── ChiselAdapter
├── GitProvider
└── ExternalKnowledgeProvider
```

RepoMarshal may need its own minimal context engine because change-aware orchestration is core to our product.

---

# 4.4 UI Skills

Canonical repository identifier:

`ibelick/ui-skills`

Study it for:

- progressive disclosure;
- skill routing;
- category discovery;
- specificity-based selection;
- minimal-context principles;
- CLI UX;
- MCP exposure;
- UI engineering skill design.

Pay particular attention to this philosophy:

> Load the smallest useful skill context.

RepoMarshal should generalize that beyond UI.

Example:

```text
Task
↓
Classify engineering domains
↓
Identify candidate skills
↓
Security-check untrusted skills
↓
Rank relevance
↓
Load minimum sufficient set
```

Avoid "skill soup."

Default target:

```text
1 skill when possible
2 when clearly justified
3 only for broad/multi-domain work
```

Do not hard-code this limit universally if evidence suggests another design.

---

# 4.5 UI/UX Pro Max

Canonical repository identifier:

`nextlevelbuilder/ui-ux-pro-max-skill`

Study it for:

- design intelligence;
- UI patterns;
- UX guidelines;
- typography;
- color systems;
- spacing;
- responsive design;
- accessibility;
- charts;
- stack-aware recommendations;
- searchable design knowledge.

Do not load a giant UI design context for every frontend task.

Determine whether RepoMarshal should:

- integrate it as an optional provider;
- query subsets;
- use its ideas to define UI quality dimensions;
- invoke it only for relevant changes.

Separate objective checks from subjective heuristics.

For example:

Objective:

- contrast threshold;
- missing labels;
- focusability;
- viewport overflow;
- console errors.

Heuristic:

- hierarchy quality;
- generic AI aesthetics;
- visual rhythm;
- overuse of cards;
- typography pairing.

Never represent subjective design opinion as deterministic correctness.

---

# 5. Mandatory Competitive Prior-Art Research

The ecosystem already contains several projects overlapping with parts of this idea.

Study them before architecture.

This is mandatory.

---

## 5.1 AgentProof

Canonical repository identifier:

`GenRamzi/AgentProof`

Study:

- independent verification;
- BASE vs HEAD comparison;
- proof tests;
- CI integrity;
- evidence receipts;
- verification policies;
- anti-test-manipulation mechanisms.

Question:

> What can RepoMarshal provide that AgentProof intentionally does not?

Do not rebuild AgentProof badly.

---

# 5.2 AgentWard

Canonical repository identifier:

`agentward-ai/agentward`

Study:

- agent permission control plane;
- runtime enforcement;
- least privilege;
- tool-call policy;
- MCP/tool interception;
- data-flow enforcement;
- audit logs.

Question:

> Should RepoMarshal integrate a runtime policy system rather than attempt to become one?

Security policy enforcement outside the model context is an important concept.

---

# 5.3 AgentSmith

Canonical repository identifier:

`PromptPartner/agentsmith`

Study this project carefully because its scope overlaps substantially with RepoMarshal.

Study:

- model-agnostic agent harness;
- project rules;
- profiles;
- definitions of done;
- verification;
- evidence;
- handoffs;
- persistent state;
- agent compatibility;
- autonomous workflows.

Question:

> What fundamentally distinguishes RepoMarshal from AgentSmith?

If the answer is weak, redesign RepoMarshal before writing substantial code.

---

# 5.4 AgentRig — Meta Harness

Canonical repository identifier:

`doidor/agentrig`

Study:

- cross-agent projection;
- AGENTS.md;
- Claude Code;
- Codex;
- Cursor;
- Copilot;
- skills;
- rules;
- MCP configuration;
- harness evaluation;
- canonical-source projection.

Question:

> Should RepoMarshal own agent-specific configuration generation, or integrate with a system like AgentRig?

Do not build redundant multi-agent configuration machinery without justification.

---

# 5.5 AgentRig — Portable Agent Plugin Ecosystem

Canonical repository identifier:

`regenrek/agentrig`

Study:

- portable plugin contracts;
- skills;
- MCP configuration;
- provenance;
- registry architecture;
- cross-agent plugin distribution.

Determine what RepoMarshal should reuse conceptually versus implement itself.

---

# 5.6 DoneProof

Canonical repository identifier:

`giugiu-a11y/doneproof`

Study:

- completion receipts;
- evidence;
- explicit remaining risks;
- human-review status;
- honest completion claims.

Important conclusion:

"Evidence before done" by itself is NOT enough to differentiate RepoMarshal.

We need something deeper.

---

# 6. Foundational Ecosystem References

Also inspect these standards/tools because they may provide better building blocks than custom implementation.

---

## 6.1 Model Context Protocol

Canonical repository identifier:

`modelcontextprotocol/modelcontextprotocol`

Study the current specification.

RepoMarshal should strongly consider MCP for interoperable capabilities.

Potential MCP tools:

```text
repomarshal_analyze_change
repomarshal_get_context
repomarshal_resolve_skills
repomarshal_audit_skill
repomarshal_plan_verification
repomarshal_run_gates
repomarshal_verify_runtime
repomarshal_get_evidence
repomarshal_completion_status
```

Do not invent a proprietary agent protocol if MCP already solves the interoperability layer adequately.

---

# 6.2 Microsoft Playwright

Canonical repository identifier:

`microsoft/playwright`

Study:

- browser automation;
- test execution;
- agent-facing tooling;
- MCP support;
- accessibility-adjacent verification;
- cross-browser capabilities.

Playwright may serve as:

- a RuntimeVerifier provider;
- a fallback when Reticle is unavailable;
- a deterministic browser-check provider.

Do not duplicate browser automation.

---

# 7. Search Beyond This List

Do not assume this list is complete.

Search current GitHub and the public web for active projects involving:

- coding-agent harnesses;
- repository agents;
- context engineering;
- repo memory;
- AI skill routing;
- AI skill registries;
- prompt-injection scanning;
- MCP security;
- MCP permissions;
- agent policy engines;
- agent verification;
- evidence receipts;
- AI definitions of done;
- runtime browser verification;
- agent observability;
- AI coding quality gates;
- code-generation governance;
- developer-agent orchestration.

Prioritize:

- actively maintained projects;
- meaningful adoption;
- recent commits;
- concrete implementation;
- real users;
- documented architecture.

Do not inflate the comparison with abandoned toy projects.

---

# 8. State-of-the-Art Matrix

Before major implementation, produce:

`docs/research/ecosystem-comparison.md`

Include a table approximately like:

| Project | Core Problem | Key Mechanism | Strength | Weakness | RepoMarshal Overlap | Integration Opportunity | License |
|---|---|---|---|---|---|---|---|

Include at least:

- SkillSpector
- Reticle
- Chisel
- UI Skills
- UI/UX Pro Max
- AgentProof
- AgentWard
- AgentSmith
- both AgentRig projects
- DoneProof
- Playwright
- relevant additional discoveries

Do not rely solely on READMEs.

Inspect important source files, tests, schemas, CLI code, issues, releases and licenses.

---

# 9. The Critical Research Question

After prior-art research, answer:

> **What important developer problem remains unsolved even if someone installs all these projects independently?**

This answer determines whether RepoMarshal deserves to exist.

Do not rationalize the project into existence.

If there is no meaningful gap, say so and redesign the concept.

---

# 10. Proposed Unique Contribution: Change-Aware Engineering Planner

Investigate this as the primary hypothesis.

RepoMarshal may provide a deterministic/adaptive planning layer that maps:

```text
Repository state
+
Requested task
+
Git diff
+
Affected architecture
+
Risk classification
+
Project policy
+
Available capabilities
+
Historical evidence
        ↓
Required Context
Required Skills
Required Security
Required Tests
Required Runtime Verification
Required UI Verification
Required Evidence
```

Call the resulting object provisionally:

# Change Plan

Example:

```yaml
task:
  description: "Change dashboard button radius"

change:
  domains:
    - frontend
    - styling

risk:
  level: low

context:
  required:
    - src/components/Button.tsx
    - design/tokens.css

skills:
  required:
    - ui-spacing

gates:
  unit_tests: targeted
  lint: required
  typecheck: required
  security: skipped
  runtime: targeted
  accessibility: targeted

evidence:
  required:
    - test-result
    - runtime-verdict

completion:
  policy: standard
```

Contrast with:

```yaml
task:
  description: "Refactor OAuth refresh-token handling"

change:
  domains:
    - backend
    - auth
    - security

risk:
  level: high

context:
  required:
    - auth architecture
    - token service
    - auth tests
    - security ADRs

skills:
  required:
    - authentication-security

gates:
  unit_tests: required
  integration_tests: required
  lint: required
  typecheck: required
  dependency_audit: required
  secrets_scan: required
  runtime: required
  ui: skipped

evidence:
  required:
    - unit-results
    - integration-results
    - runtime-verdict
    - security-report

completion:
  policy: elevated
```

The planner is potentially the core RepoMarshal innovation.

But prove that this is genuinely useful and sufficiently differentiated before committing to it.

---

# 11. Determinism Before LLM Judgment

Do not make RepoMarshal another application where an LLM decides everything.

Use deterministic signals whenever possible.

Examples:

```text
Git diff
file paths
dependency manifests
AST information
test configuration
framework detection
CI configuration
package changes
security-sensitive API usage
existing repository policies
existing architecture metadata
changed ownership boundaries
```

LLM inference may assist ambiguous classification, but core behavior should remain inspectable and reproducible.

Preferred hierarchy:

```text
deterministic rule
↓
static analysis
↓
repository metadata
↓
heuristic
↓
LLM classification only when genuinely necessary
```

Users should be able to ask:

> Why did RepoMarshal require this check?

And receive a concrete explanation.

---

# 12. Core Architecture Hypothesis

Investigate an architecture centered around:

```text
                ┌─────────────────────┐
                │    RepoMarshal CLI  │
                └──────────┬──────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │ Repository Analyzer │
                └──────────┬──────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │   Change Model   │
                  └─────────┬────────┘
                            │
                            ▼
                ┌──────────────────────┐
                │ Engineering Planner  │
                └───────────┬──────────┘
                            │
       ┌────────────────────┼─────────────────────┐
       │                    │                     │
       ▼                    ▼                     ▼
 Context Engine         Skill Router          Policy Engine
       │                    │                     │
       └────────────────────┼─────────────────────┘
                            │
                            ▼
                    Coding Agent
                            │
                            ▼
                       Git Change
                            │
                            ▼
                  Verification Plan
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
       ▼                    ▼                    ▼
   Test Gates          Security Gates      Runtime Gates
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                         UI Gates
                            │
                            ▼
                     Evidence Ledger
                            │
                            ▼
                Completion Evaluation
                            │
                ┌───────────┼────────────┐
                ▼           ▼            ▼
              PASS         FAIL        UNKNOWN
```

Challenge this architecture.

Do not implement it merely because it appears in this prompt.

---

# 13. Core Interfaces

Consider stable provider interfaces such as:

```text
RepositoryAnalyzer
ChangeClassifier
ContextProvider
SkillProvider
SecurityScanner
PolicyProvider
QualityGate
RuntimeVerifier
UIAuditor
EvidenceWriter
CompletionEvaluator
AgentAdapter
```

Providers should be replaceable.

Example:

```text
SecurityScanner
├── SkillSpector
├── Native
└── Custom
```

```text
RuntimeVerifier
├── Reticle
├── Playwright
├── HTTP
└── Custom
```

```text
ContextProvider
├── NativeRepoIndex
├── Chisel
├── Git
└── Custom
```

Avoid strong coupling to any single vendor.

---

# 14. Agent Independence

RepoMarshal must not be Claude-only.

Target interoperability with:

- Claude Code;
- OpenAI Codex;
- Cursor;
- GitHub Copilot coding agents;
- Gemini CLI;
- other MCP-capable coding agents where practical.

Claude Cloud is building the first implementation, but the architecture must remain model-agnostic.

Do not embed Claude-specific assumptions into core domain models.

Use adapters.

Potential abstraction:

```text
AgentAdapter
├── ClaudeCodeAdapter
├── CodexAdapter
├── CursorAdapter
└── GenericMCPAdapter
```

Only implement adapters necessary for v0.1.

---

# 15. Context Engine

One major objective is reducing wasted model context.

RepoMarshal should not dump the repository into every prompt.

Potential flow:

```text
Task
↓
Changed/likely-affected subsystem
↓
Repository dependency relationships
↓
Relevant specs/ADRs
↓
Relevant tests
↓
Relevant implementation
↓
Context budget
↓
Rank
↓
Select
```

Potential CLI:

```text
repomarshal context "implement OAuth callback"
```

Possible output:

```text
Selected context

1. docs/adr/014-oauth.md
2. src/auth/callback.ts
3. src/auth/token-service.ts
4. tests/auth/callback.test.ts

Estimated selected context: 6,240 tokens
Estimated candidate context: 54,810 tokens
Estimated avoided context: 48,570 tokens
```

Do not claim exact token savings unless measurement supports them.

Clearly label estimates.

Use actual tokenizer libraries where reasonable.

Allow token measurement to be disabled.

---

# 16. Skill Router

Generalize UI Skills' progressive-disclosure philosophy.

Potential command:

```text
repomarshal skills resolve "<task>"
```

Example:

```text
Task:
"Improve mobile navigation accessibility"

Selected:
1. accessibility
2. responsive-navigation

Rejected:
- landing-page-design
- charts
- ecommerce
- animation

Reason:
No evidence these domains are involved.
```

Skill selection should consider:

- task;
- affected files;
- stack;
- framework;
- risk;
- repository conventions.

External/untrusted skills should be security-scanned before activation.

---

# 17. Security Intake

Treat external agent capabilities as untrusted input.

Potential targets:

- SKILL.md;
- agent skills;
- MCP servers;
- plugins;
- downloaded instruction bundles;
- external scripts;
- configuration packages.

Potential command:

```text
repomarshal skill audit <target>
```

Potential output:

```text
Risk: HIGH

Findings:
- attempts environment enumeration
- reads SSH-related paths
- invokes shell
- sends network requests
- instruction override pattern

Decision:
BLOCK
```

Do not falsely claim complete safety.

Security results should use language such as:

```text
ALLOW
REVIEW
BLOCK
UNKNOWN
```

with supporting findings.

SkillSpector should be investigated as the preferred scanner provider.

---

# 18. Threat Model

Create:

`docs/security/threat-model.md`

Include at least:

- malicious SKILL.md;
- indirect prompt injection;
- tool poisoning;
- MCP poisoning;
- excessive permissions;
- dependency confusion;
- malicious install scripts;
- compromised package;
- environment-variable theft;
- SSH key access;
- browser credential access;
- network exfiltration;
- poisoned repository instructions;
- malicious Git hooks;
- symlink attacks;
- path traversal;
- command injection;
- CI credential exposure;
- compromised evidence;
- verification bypass;
- test deletion;
- narrowed test discovery;
- skipped tests;
- altered CI configuration;
- fabricated reports;
- stale cached evidence.

Distinguish:

- threat detection;
- policy enforcement;
- sandboxing;
- verification.

Do not claim RepoMarshal provides sandboxing unless it genuinely does.

---

# 19. Change-Aware Quality Gates

A major feature should be avoiding unnecessary checks.

Example:

```text
README-only change

lint              SKIP
typecheck          SKIP
browser            SKIP
security           SKIP
docs check         RUN
```

Example:

```text
React component

lint               RUN
typecheck           RUN
targeted tests      RUN
browser runtime     RUN
accessibility       RUN
security            conditional
```

Example:

```text
package-lock / dependencies

dependency audit    RUN
supply-chain scan   RUN
tests               RUN
runtime             conditional
```

Example:

```text
authentication backend

lint                RUN
typecheck           RUN
unit                RUN
integration         RUN
security            RUN
secrets             RUN
runtime             RUN
UI                  SKIP
```

Every decision must have a reason.

---

# 20. Incremental Execution and Cache

Do not repeat expensive checks unnecessarily.

Investigate caching based on:

- Git commit;
- changed files;
- content hashes;
- config hashes;
- dependency lockfiles;
- test inputs;
- tool versions;
- environment fingerprint.

Example:

```text
Gate               Status

lint               cached
unit-auth          rerun
integration-auth   rerun
UI                 not applicable
dependency audit   cached
```

Cache invalidation correctness matters more than optimistic reuse.

Never reuse stale security or verification evidence incorrectly.

---

# 21. Runtime Verification

Runtime verification must be evidence-based.

Potential command:

```text
repomarshal verify
```

Possible output:

```text
Application boot        PASS
Health endpoint         PASS
Login flow              PASS
Settings persistence    FAIL
Console errors          PASS

OVERALL                 FAIL
```

If a verifier cannot inspect something:

```text
UNKNOWN
```

Do not infer success from missing evidence.

Reticle should be investigated as one provider.

Playwright should be investigated as another.

---

# 22. UI / UX Quality

Only invoke UI intelligence when relevant.

Potential checks:

- accessibility;
- keyboard navigation;
- labels;
- contrast;
- viewport overflow;
- responsive behavior;
- component consistency;
- spacing-system violations;
- token usage;
- loading states;
- error states;
- empty states;
- component duplication.

Potential command:

```text
repomarshal ui audit
```

Separate results into:

```text
DETERMINISTIC
HEURISTIC
ADVISORY
```

Do not pretend aesthetics are mathematically provable.

---

# 23. Anti-Sludge Engineering Rules

RepoMarshal should help prevent low-quality AI-generated code.

Detect or discourage:

- duplicate helpers;
- duplicate services;
- duplicate components;
- unnecessary wrappers;
- unnecessary abstractions;
- unnecessary dependencies;
- dead code;
- giant generated files;
- excessive comments;
- random README files;
- TODO dumping;
- unimplemented placeholders;
- fake mocks used as implementation;
- silent exception swallowing;
- fallback chains hiding bugs;
- `any`/type weakening to bypass failures;
- disabled tests;
- skipped tests;
- reduced test coverage;
- narrowed CI;
- copied code already present elsewhere.

Before a coding agent creates a new abstraction, RepoMarshal should ideally help it answer:

> Does an equivalent abstraction already exist?

Do not turn this into a noisy style police system.

Focus on high-confidence duplication or policy violations.

---

# 24. Completion Contract

Tasks should be evaluated against explicit completion requirements.

Possible configuration:

```yaml
completion:
  tests:
    required_when_applicable: true

  lint:
    required_when_configured: true

  typecheck:
    required_when_configured: true

  runtime:
    change_aware: true

  security:
    risk_aware: true

  ui:
    required_when_frontend_affected: true

  evidence:
    required: true
```

Potential command:

```text
repomarshal done
```

Potential result:

```text
TASK COMPLETION

Implementation          PASS
Unit Tests              PASS
Integration Tests       PASS
Lint                    PASS
Type Check              PASS
Security                PASS
Runtime                 FAIL
Accessibility           PASS

OVERALL                 FAIL

Reason:
Settings update produced HTTP 500.
```

The important principle:

> An agent's claim of completion is not evidence of completion.

---

# 25. Evidence Ledger

Create a local traceable evidence system.

Potential structure:

```text
.repomarshal/
├── config/
├── cache/
├── context/
├── plans/
├── policies/
├── evidence/
└── state/
```

For each verification cycle record:

- task;
- repository commit;
- base commit;
- changed files;
- change classification;
- risk level;
- context selected;
- skills selected;
- security decisions;
- gates selected;
- gates skipped;
- reasons;
- commands executed;
- exit codes;
- tool versions;
- runtime evidence;
- warnings;
- unresolved unknowns;
- completion verdict.

Reports must not contain secrets.

Implement redaction.

---

# 26. Evidence Integrity

Do not blindly trust evidence generated by the coding agent itself.

Investigate stronger evidence approaches:

- deterministic command execution;
- captured exit code;
- stdout/stderr digest;
- file hashes;
- commit hashes;
- environment fingerprints;
- verifier provenance;
- BASE/HEAD comparison;
- signed or hash-linked evidence records.

Do not overengineer cryptography in v0.1.

But design evidence so fabrication becomes harder.

AgentProof is especially relevant prior art here.

---

# 27. Configuration

Investigate a single declarative config:

```text
repomarshal.yaml
```

Potential schema:

```yaml
project:
  name: example
  privacy: local-first

agents:
  compatibility:
    - claude-code
    - codex
    - cursor

planner:
  mode: adaptive

context:
  strategy: targeted
  token_budget: 12000

skills:
  activation: on-demand
  security_scan: required
  preferred_max_active: 3

security:
  skill_scan: required
  dependency_audit: adaptive
  secrets_scan: adaptive

verification:
  runtime: adaptive

quality:
  lint: autodetect
  typecheck: autodetect
  tests: autodetect

ui:
  audit: adaptive
  accessibility: required_when_applicable

evidence:
  enabled: true
  redact_secrets: true

completion:
  require_evidence: true
```

Treat this as a hypothesis, not a fixed schema.

Use schema validation.

Version the config format.

---

# 28. CLI UX

Investigate a small coherent CLI.

Candidate commands:

```text
repomarshal init
repomarshal doctor

repomarshal analyze
repomarshal plan "<task>"

repomarshal context "<task>"

repomarshal skills list
repomarshal skills resolve "<task>"
repomarshal skills audit <target>

repomarshal verify
repomarshal audit
repomarshal ui audit

repomarshal evidence
repomarshal status
repomarshal done
```

Do not implement commands just because they look impressive.

v0.1 should contain only commands needed for the primary workflow.

Machine-readable JSON output is important.

Consider:

```text
--json
```

for every significant command.

---

# 29. Doctor

A high-quality:

```text
repomarshal doctor
```

should inspect the local project and report useful facts such as:

- Git availability;
- repository state;
- language(s);
- framework(s);
- package manager(s);
- test framework;
- linter;
- formatter;
- type checker;
- CI configuration;
- browser framework;
- SkillSpector availability;
- Reticle availability;
- Playwright availability;
- MCP compatibility;
- RepoMarshal configuration validity.

Output should be actionable rather than verbose.

---

# 30. Technology Stack Decision

Do NOT automatically select a language.

Evaluate at least:

### Rust

Pros:

- fast startup;
- single binaries;
- safe systems code;
- good CLI ecosystem.

Cons:

- contributor barrier;
- integrations may require subprocesses.

### Python

Pros:

- security ecosystem;
- AI tooling ecosystem;
- rapid development;
- strong parsing/static-analysis ecosystem.

Cons:

- distribution/runtime issues.

### TypeScript

Pros:

- MCP ecosystem;
- frontend/runtime ecosystem;
- Node package distribution;
- Playwright integration.

Cons:

- runtime requirement;
- slower CLI startup than native.

### Go

Pros:

- single binaries;
- concurrency;
- cross-platform distribution.

Consider hybrid architectures only if genuinely justified.

Produce:

`docs/adr/001-core-language.md`

Choose based on engineering tradeoffs, not fashion.

---

# 31. Required ADRs

Create at least:

```text
docs/adr/001-core-language.md
docs/adr/002-provider-integration.md
docs/adr/003-agent-interoperability.md
docs/adr/004-evidence-model.md
docs/adr/005-change-planner.md
```

Use concise ADR format:

```text
Context
Decision
Alternatives
Consequences
```

---

# 32. Licensing and Provenance

This is a hard requirement.

Create:

`docs/UPSTREAM_PROVENANCE.md`

For every upstream project record:

- repository identifier;
- commit SHA inspected;
- date inspected;
- license;
- relevant component license;
- functionality studied;
- integration method;
- whether code was reused;
- attribution obligations;
- compatibility concerns.

Before copying any code:

1. inspect LICENSE;
2. inspect component-specific license;
3. inspect NOTICE;
4. inspect package metadata;
5. inspect source headers;
6. inspect contribution documentation.

Preferred integration order:

```text
1. documented protocol/API
2. MCP integration
3. CLI/subprocess adapter
4. plugin/provider interface
5. clean independent implementation from ideas/specs
6. direct code reuse only when clearly legal and justified
```

Do NOT copy code merely because the repository is public.

---

# 33. Special License Warnings

Verify these statements against the current repository state before relying on them:

- Chisel has used FSL-1.1-Apache-2.0.
- Reticle has had different licensing for SDK/adapters versus server components.
- UI Skills has used MIT.
- UI/UX Pro Max has identified itself as MIT.
- AgentSmith has identified itself as MIT.

Licenses can change.

Record exact commits.

If a source-available license restricts competing offerings, avoid incorporating restricted implementation.

Architecture must survive without legal ambiguity.

---

# 34. Local-First Privacy

Default behavior should preserve source privacy.

Principles:

```text
repository source stays local
context index stays local
evidence stays local
security scans stay local when possible
telemetry minimal or disabled
external AI calls opt-in
```

Never upload repository contents merely to obtain convenience features without explicit configuration.

Do not store secrets.

Redact:

- API keys;
- tokens;
- credentials;
- cookies;
- private keys;
- authorization headers;
- connection strings where identifiable.

---

# 35. Git Awareness

RepoMarshal should understand:

```text
git status
git diff
git diff --staged
git log
git branch
git merge-base
```

Use Git to understand:

- BASE;
- HEAD;
- changed files;
- new dependencies;
- deleted tests;
- modified CI;
- modified permissions;
- changed ownership boundaries.

Do not mutate Git state unnecessarily.

---

# 36. Repository Safety

While developing RepoMarshal:

Do NOT:

- force push;
- rewrite existing history;
- delete branches casually;
- expose credentials;
- disable repository protection;
- commit secrets;
- alter upstream repositories.

If a feature branch is supported, use a sensible implementation branch.

Otherwise work in the checked-out repository without destructive Git actions.

Make coherent commits.

---

# 37. Testing Strategy

RepoMarshal itself must be heavily tested.

Create realistic fixtures.

Potential structure:

```text
tests/
fixtures/
├── docs-only-change/
├── python-backend/
├── react-app/
├── auth-change/
├── dependency-change/
├── safe-skill/
├── malicious-skill/
├── broken-web-app/
└── reduced-test-scope/
```

Important behavioral tests:

```text
docs-only change
→ expensive runtime checks not selected

React UI change
→ frontend/runtime/UI gates selected

auth change
→ elevated security gates selected

dependency change
→ supply-chain gate selected

malicious skill
→ blocked/review result

broken app
→ runtime FAIL

unobservable app state
→ UNKNOWN, never PASS

deleted tests
→ suspicious/high-risk result

narrowed CI scope
→ suspicious/high-risk result
```

---

# 38. CI

Set up only useful workflows.

Likely:

- formatting;
- lint;
- type checking;
- unit tests;
- integration tests;
- dependency audit;
- secret scan;
- build verification.

Avoid CI theatre.

Every workflow should protect something meaningful.

Use least-privilege GitHub Actions permissions.

Pin third-party actions appropriately.

---

# 39. Anti-Supply-Chain Rules

RepoMarshal is a security-adjacent developer tool.

Be conservative with dependencies.

Every new dependency requires a reason.

Prefer:

- mature packages;
- active maintenance;
- limited transitive dependencies;
- pinned lockfiles;
- provenance where available.

Do not add a dependency for trivial functionality.

---

# 40. Popularity and Adoption Goal

The aspiration is for RepoMarshal to become widely used globally.

Do not optimize for superficial GitHub stars.

Optimize for developers keeping the tool installed.

Critical adoption properties:

### Immediate value

A developer should understand the value within 30 seconds.

### Fast onboarding

Aim eventually for:

```text
repomarshal init
repomarshal doctor
repomarshal plan "my task"
```

### No vendor lock-in

Support multiple coding agents.

### Local-first

Respect private repositories.

### Explainability

Every required/skipped gate should have a reason.

### Speed

Incremental execution matters.

### Excellent CLI

Readable human output plus reliable JSON.

### Composability

Integrate existing excellent tools instead of replacing them badly.

### Trust

Be precise about what RepoMarshal can and cannot prove.

---

# 41. README Standard

README should quickly explain:

```text
# RepoMarshal

Marshal every AI-generated change from context to proof.

[one-paragraph problem]

[small diagram]

Quick Start

What RepoMarshal decides

How Change-Aware Engineering works

Providers

Supported agents

Security

Evidence

Examples

Roadmap

Contributing

License
```

Avoid:

- excessive badges;
- exaggerated AI marketing;
- fake metrics;
- unsupported benchmarks;
- "revolutionary";
- claims of complete security;
- claims of eliminating hallucinations.

---

# 42. Demonstration Scenario

The first convincing vertical slice should prove adaptive behavior.

Use at least three fixture scenarios.

## Scenario A — Documentation

Task:

```text
Update installation documentation.
```

Expected:

```text
Context        minimal
UI skill       no
Security       no
Runtime        no
Docs check     yes
```

---

## Scenario B — Frontend

Task:

```text
Improve responsive login form.
```

Expected:

```text
Frontend context        yes
Relevant UI skill       yes
Skill audit             if external
Lint                    yes
Typecheck               yes
Frontend tests          yes
Runtime verifier        yes
Accessibility           yes
Backend security        no unless affected
```

---

## Scenario C — Authentication

Task:

```text
Modify refresh-token handling.
```

Expected:

```text
Auth context            yes
Security skill          yes
Security scan           yes
Unit tests              yes
Integration tests       yes
Secrets check           yes
Runtime                 yes
UI                      no unless frontend changed
Evidence level          elevated
```

The difference between these plans is the product.

---

# 43. Measurable Evaluation

Define meaningful metrics.

Possible:

### Context efficiency

```text
candidate repository context
vs.
selected context
```

### Planner precision

For fixture scenarios:

```text
required gate selected?
unnecessary expensive gate avoided?
```

### Verification honesty

```text
broken fixture detected?
unknown correctly preserved?
```

### Performance

```text
cold planning latency
warm planning latency
incremental gate latency
```

### Security

```text
malicious fixture findings
false positives
false negatives
```

Do not fabricate benchmark numbers.

---

# 44. Native vs Provider Functionality

For each capability, explicitly decide:

```text
NATIVE
PROVIDER
OPTIONAL PROVIDER
OUT OF SCOPE
```

Example:

| Capability | Likely Strategy |
|---|---|
| Change classification | Native |
| Engineering plan | Native |
| Git analysis | Native |
| Evidence model | Native |
| Completion evaluation | Native |
| Context ranking | Native/core |
| Deep skill security | SkillSpector provider |
| Runtime perception | Reticle provider |
| Browser fallback | Playwright provider |
| Project knowledge | Native + possible Chisel provider |
| UI knowledge | UI Skills/UIUX providers |
| Runtime policy enforcement | likely external provider |

Do not assume this table is correct.

Research first.

---

# 45. RepoMarshal's Boundary

RepoMarshal probably should NOT become:

- a coding model;
- an IDE;
- a complete browser automation engine;
- a new security scanner replacing SkillSpector;
- a new Playwright;
- a new Reticle;
- a giant UI prompt library;
- another MCP registry;
- another agent package manager;
- an autonomous GitHub maintainer.

Its differentiator should likely be:

> **intelligent repository-aware orchestration of existing engineering capabilities.**

But prove this.

---

# 46. Architecture Deliverables Before Major Coding

Produce:

```text
docs/research/ecosystem-comparison.md
docs/research/gap-analysis.md

docs/architecture.md
docs/security/threat-model.md
docs/UPSTREAM_PROVENANCE.md

docs/adr/001-core-language.md
docs/adr/002-provider-integration.md
docs/adr/003-agent-interoperability.md
docs/adr/004-evidence-model.md
docs/adr/005-change-planner.md
```

Keep them useful, not bloated.

---

# 47. Gap Analysis

`docs/research/gap-analysis.md` must directly answer:

### Question 1

What does installing all five primary tools independently fail to provide?

### Question 2

What do AgentProof, AgentSmith, AgentWard, AgentRig and DoneProof already solve?

### Question 3

Where would RepoMarshal duplicate them?

### Question 4

What is the smallest truly novel layer?

### Question 5

Why would a developer install RepoMarshal instead of one competitor?

### Question 6

Why would existing tool maintainers want RepoMarshal integration rather than view RepoMarshal as a hostile clone?

---

# 48. Unique Value Proposition Test

Before substantial implementation, produce one sentence:

> Unlike ________, RepoMarshal ________.

It must contain a concrete mechanism.

Bad:

> RepoMarshal makes AI coding safer and better.

Bad:

> RepoMarshal combines the best tools.

Better conceptually:

> RepoMarshal analyzes each repository change and deterministically selects the minimum context, skills, permissions and verification gates required to produce an evidence-backed completion verdict.

Refine this after research.

If the sentence remains weak, stop adding features and rethink the architecture.

---

# 49. MVP Discipline

Do not attempt everything.

Define v0.1 after research.

A plausible v0.1 vertical slice may contain:

1. repository detection;
2. Git diff analysis;
3. change classification;
4. risk classification;
5. engineering-plan generation;
6. test/lint/typecheck autodetection;
7. adaptive gate selection;
8. provider interface;
9. one security-provider integration or stub with explicit availability;
10. one runtime-provider integration or deterministic fallback;
11. evidence recording;
12. completion evaluation;
13. JSON output;
14. fixture-based tests.

Do not implement five shallow integrations if two real integrations prove the architecture better.

---

# 50. No Fake Integrations

This rule is critical.

Do not create classes named:

```text
SkillSpectorAdapter
ReticleAdapter
ChiselAdapter
```

that merely return:

```text
TODO
mock success
always true
```

and then advertise support.

Integration status must be explicit:

```text
SUPPORTED
EXPERIMENTAL
PLANNED
UNAVAILABLE
```

A provider that is not installed should say so.

A failing provider should fail honestly.

---

# 51. No Fake AI Features

Do not invent an "AI context optimizer" that simply picks filenames with keywords.

If v0.1 uses heuristic ranking, label it honestly.

Example:

```text
Context selection:
deterministic path/dependency heuristic
```

Later versions may add optional semantic ranking.

Transparency matters.

---

# 52. Machine-Readable Contracts

Define schemas for core objects early.

Likely:

```text
ChangeAnalysis
RiskAssessment
ContextPlan
SkillPlan
GatePlan
VerificationResult
EvidenceRecord
CompletionVerdict
```

Use explicit versioning.

Example:

```json
{
  "schema_version": "0.1",
  "verdict": "fail"
}
```

This will matter for:

- MCP;
- CI;
- GitHub Actions;
- third-party adapters;
- future SDKs.

---

# 53. Completion Semantics

Avoid ambiguous Boolean-only semantics.

Possible overall states:

```text
PASS
FAIL
UNKNOWN
BLOCKED
```

Define precisely.

For example:

### PASS

All required gates satisfied.

### FAIL

One or more required gates produced negative evidence.

### UNKNOWN

Required evidence could not be obtained reliably.

### BLOCKED

Policy prevents execution or completion.

Do not blur these states.

---

# 54. Human Override

Investigate explicit overrides for exceptional cases.

Example:

```text
repomarshal override --gate runtime --reason "hardware unavailable"
```

Overrides should:

- be explicit;
- require reason;
- be recorded in evidence;
- not silently transform evidence to PASS.

Possible status:

```text
WAIVED
```

Completion policies may decide whether waived gates permit overall completion.

---

# 55. Explainability

Potential command:

```text
repomarshal plan --explain
```

Example:

```text
SECURITY SCAN: REQUIRED

Reasons:
- package-lock.json changed
- new dependency introduced
- task touches authentication subsystem

UI AUDIT: SKIPPED

Reason:
- no frontend-owned files changed
```

This explainability is potentially a major product strength.

---

# 56. Dry Run

Support a mode such as:

```text
repomarshal plan --dry-run
```

RepoMarshal should show what it intends to execute before running external tools.

Especially important for:

- security;
- shell;
- browser;
- external providers;
- network access.

---

# 57. Offline Behavior

Investigate useful offline operation.

Core functionality should ideally still provide:

- Git analysis;
- repository classification;
- deterministic planning;
- configured quality gates;
- evidence recording.

External providers can degrade gracefully.

Do not make basic RepoMarshal usage depend on cloud APIs.

---

# 58. Installation Goal

Eventually aim for one straightforward installation mechanism appropriate to the chosen stack.

Do not implement installers before core architecture is proven.

Prioritize:

1. correct core;
2. tests;
3. stable CLI;
4. packaging;
5. convenience installer.

---

# 59. Repository Structure

Do not create dozens of empty modules.

Derive structure from the architecture.

A conceptual example:

```text
repo-marshal/
├── src/
│   └── repomarshal/
│       ├── cli/
│       ├── repository/
│       ├── planner/
│       ├── context/
│       ├── skills/
│       ├── security/
│       ├── gates/
│       ├── verification/
│       ├── evidence/
│       ├── policies/
│       ├── providers/
│       └── schemas/
├── tests/
├── docs/
├── examples/
└── fixtures/
```

This is not mandatory.

Use the idioms of the chosen language.

---

# 60. Engineering Quality Rules

While implementing:

1. Search before creating.
2. Read before editing.
3. Keep functions focused.
4. Keep interfaces narrow.
5. Avoid giant files.
6. Avoid premature abstraction.
7. Avoid speculative features.
8. Avoid needless dependencies.
9. Never swallow exceptions silently.
10. Use structured errors.
11. Test important behavior.
12. Do not weaken tests to get green.
13. Do not disable type checks.
14. Do not claim support that does not exist.
15. Do not create placeholder documentation presented as finished.
16. Do not add generated sludge.
17. Keep commits coherent.
18. Keep provenance clear.

---

# 61. Research Method

For each upstream repository:

1. inspect README;
2. inspect LICENSE;
3. capture commit SHA;
4. inspect package metadata;
5. inspect source layout;
6. inspect primary CLI implementation;
7. inspect public interfaces;
8. inspect tests;
9. inspect schemas;
10. inspect latest meaningful issues;
11. inspect recent releases/tags;
12. inspect architecture documentation;
13. inspect integration mechanisms.

Record factual conclusions.

Distinguish:

```text
VERIFIED
INFERRED
UNKNOWN
```

Do not present inference as fact.

---

# 62. First Phase — Research Only

Begin by researching the ecosystem.

Do not immediately generate 5,000 lines of code.

Produce the research and gap documents.

Then make an explicit internal architecture decision.

You do not need to wait for human confirmation unless a genuinely blocking ambiguity or authorization problem exists.

Proceed through the phases in the same cloud session when practical.

---

# 63. Phase 2 — Product Definition

After research, define:

### Product category

What precisely is RepoMarshal?

### Primary user

Who experiences the pain?

### Trigger

When do they run RepoMarshal?

### Input

What does RepoMarshal consume?

### Decision

What does RepoMarshal determine?

### Output

What artifact or action does it produce?

### Value

Why is it substantially better than manually installing other tools?

### Non-goals

What does RepoMarshal explicitly refuse to become?

Write:

`docs/product-definition.md`

---

# 64. Phase 3 — Architecture

Produce:

`docs/architecture.md`

Include:

- component diagram;
- data flow;
- core entities;
- provider system;
- planning algorithm;
- persistence;
- evidence flow;
- failure semantics;
- caching;
- privacy boundary;
- security boundary;
- MCP interoperability.

Use Mermaid where useful.

---

# 65. Phase 4 — Threat Model

Produce:

`docs/security/threat-model.md`

Use realistic attacker scenarios.

Prioritize risks.

Separate v0.1 mitigations from future mitigations.

---

# 66. Phase 5 — MVP Decision

Define v0.1 with:

```text
MUST
SHOULD
MAY
NOT NOW
```

Do not let v0.1 exceed the smallest convincing end-to-end workflow.

Write:

`docs/roadmap.md`

---

# 67. Phase 6 — Implement Vertical Slice

Only then build.

A successful initial vertical slice should look conceptually like:

```text
repomarshal init

repomarshal doctor

repomarshal plan "modify authentication callback"
```

Output:

```text
CHANGE ANALYSIS

Domains:
- backend
- auth
- security

Risk:
HIGH

CONTEXT

Required:
- auth architecture
- callback implementation
- auth tests

SKILLS

Recommended:
- security/authentication

GATES

Lint                    required
Type Check              required
Unit Tests              required
Integration Tests       required
Security Scan           required
Runtime Verification    required
UI Audit                not applicable

EVIDENCE POLICY

Level:
elevated
```

Then execute or simulate only what is legitimately available in v0.1.

No fake success.

---

# 68. Phase 7 — Integration

Implement real provider integration only where justified.

Priority candidates:

### SkillSpector

Use as external security provider if available and licensing/API design supports it.

### Reticle

Use as runtime-verification provider if available and licensing/interface design supports it.

### Playwright

Use as deterministic browser provider/fallback if appropriate.

Do not make Chisel/UI systems hard dependencies unless research supports it.

---

# 69. Phase 8 — Evidence

Implement real evidence recording for the vertical slice.

Example:

```json
{
  "schema_version": "0.1",
  "task": "Modify authentication callback",
  "base_commit": "...",
  "head_commit": "...",
  "change_domains": [
    "backend",
    "auth",
    "security"
  ],
  "risk": "high",
  "gates": [],
  "verdict": "unknown"
}
```

Do not use fabricated evidence.

---

# 70. Phase 9 — Hostile Self-Review

Before declaring the initial project complete, attack your own design.

Answer:

### Architecture

- Is RepoMarshal really necessary?
- Is it just another harness?
- What functionality duplicates competitors?
- What belongs upstream instead?

### Security

- Could a malicious repo trick the planner?
- Could a malicious skill alter policy?
- Can evidence be forged?
- Can cached evidence become stale?
- Can command configuration inject shell syntax?

### Correctness

- Can UNKNOWN become PASS?
- Can skipped gates be mistaken for successful gates?
- Can a changed file avoid classification?

### Context

- Could context selection omit a critical file?
- Could token-efficiency optimization sacrifice correctness?

### UX

- Does `init` modify too much?
- Is configuration burdensome?
- Are errors understandable?

### Maintenance

- What breaks when SkillSpector changes?
- What breaks when Reticle changes?
- What breaks when MCP evolves?

Fix high-severity findings.

---

# 71. Phase 10 — Run Everything

Before finalizing:

- formatter;
- linter;
- type checker;
- unit tests;
- integration tests;
- fixture tests;
- security checks;
- CLI smoke tests.

Record actual commands and results.

Do not claim a check ran if it did not.

---

# 72. Git and Commit Strategy

Use clear commits.

Possible progression:

```text
docs: research agent engineering prior art
docs: define RepoMarshal architecture and threat model
feat: add repository analyzer and change model
feat: add adaptive engineering planner
feat: add evidence and completion models
feat: add first verification providers
test: add change-aware planning fixtures
docs: add quickstart and architecture overview
```

Actual commits should reflect actual work.

Do not manufacture commit history simply to resemble this list.

Never force push.

---

# 73. README After the Vertical Slice

Only after something real works, update README.

It should answer:

### What is RepoMarshal?

One sentence.

### What pain does it solve?

One short paragraph.

### Why not install five tools?

Explain the planning/orchestration gap.

### What happens?

Show:

```text
change → analyze → plan → execute → verify → evidence
```

### Quick Start

Show actual working commands only.

### Example

Show one real fixture output.

### Integrations

Clearly label:

```text
Supported
Experimental
Planned
```

### Security

Explain boundaries honestly.

---

# 74. Contribution Strategy

For every capability ask:

> Should this live inside RepoMarshal or be contributed upstream?

Potential upstream contributions might include:

- standardized JSON output;
- provider hooks;
- MCP interoperability;
- stable schemas;
- improved CLI automation;
- provenance metadata.

RepoMarshal should cooperate with the ecosystem.

Do not unnecessarily fork functionality.

---

# 75. Future Architectural Possibility

Investigate whether the long-term ecosystem could look like:

```text
                       Coding Agent
                            │
                            ▼
                       RepoMarshal
                            │
                   Change-Aware Plan
                            │
        ┌───────────────────┼────────────────────┐
        ▼                   ▼                    ▼
 Skill Providers      Security Providers     Context Providers
        │                   │                    │
  UI Skills           SkillSpector              Chisel
 UI/UX Pro Max          AgentWard               Native
        │                   │                    │
        └───────────────────┼────────────────────┘
                            │
                            ▼
                      Implementation
                            │
                            ▼
                     Verification Plan
                            │
                ┌───────────┴───────────┐
                ▼                       ▼
             Reticle                Playwright
                │                       │
                └───────────┬───────────┘
                            ▼
                         Evidence
                            │
                            ▼
                 Completion Evaluation
```

This is a hypothesis, not a mandate.

---

# 76. The Standard for Originality

Do not measure originality by whether the source files are newly typed.

Measure originality by whether RepoMarshal introduces a useful new mechanism.

The promising mechanism is:

> **repository-aware change classification that dynamically compiles a minimum sufficient engineering plan from context, skill, security, testing, runtime, UI and evidence policies.**

This can eventually become something analogous to a compiler:

```text
Task + Diff + Repo Policy
          │
          ▼
   RepoMarshal Planner
          │
          ▼
 Engineering Execution Plan
```

Think deeply about this model.

A compiler-like architecture may be more robust than a collection of ad-hoc agent prompts.

---

# 77. Potential Internal Compiler Model

Investigate:

```text
SOURCE

Task
Git diff
Repo metadata
Policies
Available providers

        ↓

ANALYSIS

Domain detection
Risk classification
Dependency impact
Change semantics

        ↓

INTERMEDIATE REPRESENTATION

ChangePlan IR

        ↓

PLANNING

Context plan
Skill plan
Security plan
Gate plan
Evidence plan

        ↓

EXECUTION

Providers

        ↓

RESULT

Evidence graph

        ↓

EVALUATION

Completion verdict
```

If useful, define a small versioned internal representation.

This could become one of RepoMarshal's most defensible architectural elements.

Do not overengineer it in v0.1.

---

# 78. Avoid Agent Token Waste

Token efficiency matters, but correctness comes first.

Optimize:

- context retrieval;
- skill loading;
- tool descriptions;
- repeated repository exploration;
- duplicated instruction files;
- unnecessarily verbose agent output.

Measure when possible.

Do not optimize:

```text
100 fewer tokens
```

at the cost of missing critical architectural context.

Report:

```text
selected
excluded
reason
estimated size
```

so users can understand context decisions.

---

# 79. Cross-Session Memory

RepoMarshal may eventually preserve high-value project decisions between agent sessions.

But do not build an unbounded memory database.

Prefer durable repository knowledge:

- ADRs;
- specs;
- policies;
- verified facts;
- evidence;
- explicit state.

Avoid storing arbitrary conversational noise.

---

# 80. Agent Instructions

If RepoMarshal generates agent instructions, keep them concise.

Do not produce:

```text
CLAUDE.md = 20,000 tokens
AGENTS.md = 20,000 tokens
.cursor/rules = same 20,000 tokens
```

Consider:

```text
small bootstrap instructions
↓
agent calls RepoMarshal
↓
RepoMarshal returns task-specific context
```

This aligns with our token-efficiency objective.

---

# 81. Success Definition for This Claude Session

This session succeeds if it leaves `MHHamdan/repo-marshal` with:

1. serious upstream/prior-art research;
2. current license/provenance analysis;
3. a defensible gap analysis;
4. a clear product definition;
5. architecture;
6. threat model;
7. ADRs;
8. focused v0.1 scope;
9. a working vertical slice;
10. meaningful automated tests;
11. honest README;
12. no fake integrations;
13. no copied incompatible source;
14. coherent commits;
15. a clear next-step roadmap.

It does NOT succeed merely by generating many files.

---

# 82. Final Decision Gate

Before finishing, answer explicitly:

### Does RepoMarshal deserve to exist?

Give a rigorous answer.

Then answer:

### What is its one strongest differentiator?

Then:

### What functionality did we deliberately NOT build because another project already does it better?

Then:

### What is the most important thing to implement next?

Then:

### What would prevent this project from achieving broad adoption?

Do not give flattering answers.

Use the evidence from research and implementation.

---

# 83. Final Session Report

At the end, provide a concise report containing:

## Research

What projects were studied.

## Differentiation

What unique gap RepoMarshal addresses.

## Architecture

What was chosen and why.

## Implementation

What actually works.

## Integrations

For each:

```text
SkillSpector
Reticle
Chisel
UI Skills
UI/UX Pro Max
Playwright
```

mark:

```text
SUPPORTED
EXPERIMENTAL
PLANNED
NOT REQUIRED
```

## Tests

Exact commands and results.

## Security

Highest remaining risks.

## Licensing

Any unresolved concerns.

## Repository

Files/commits created.

## Next milestone

The single highest-value next step.

---

# 84. Begin

Start by inspecting the current `MHHamdan/repo-marshal` repository.

Confirm:

- current branch;
- current files;
- current Git status;
- current remote;
- initial commit.

Then begin Phase 1 research.

Do not start by generating implementation scaffolding.

Research the upstream ecosystem first.

Use exact current repository versions and record commit SHAs.

Challenge the original premise aggressively.

If a proposed feature already exists elsewhere and an adapter would be better, integrate rather than clone.

If a proposed feature adds complexity without significant user value, remove it.

If the architecture changes as evidence accumulates, document why.

The goal is not to preserve this prompt.

The goal is to create the strongest possible RepoMarshal.
