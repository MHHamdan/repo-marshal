# Gap analysis

_Phase 1 conclusion, 2026-10-13. The evidence is in [`ecosystem-comparison.md`](ecosystem-comparison.md) and
[`notes/`](notes/). This document challenges the MISSION.md premise and does not assume it is right._

## Summary

- **The original premise is partly wrong.** "Diff-driven selection of required checks for coding agents"
  already exists (Preloop, Sept 2026). "A verification plan where every gate has a reason" also exists
  (AgentSmith), just not per change. "Evidence before done" exists three times over (AgentProof, DoneProof,
  AgentSmith receipts).
- **A narrower gap remains, and it is real.** No local, agent-agnostic tool compiles a git diff into an
  explained plan with no configuration. That plan should cover which context, skills, gates and evidence
  the change needs, why every other gate was skipped, and a fail-closed verdict that treats UNKNOWN as
  first-class.
- **RepoMarshal deserves to exist only as that narrow engine:** a *change → plan compiler* and verdict
  evaluator that other harnesses can embed. It must not become another harness.

---

## Q1. What does installing all five primary tools independently fail to provide?

The five tools are SkillSpector, Reticle, Chisel, UI Skills and UI/UX Pro Max.

1. **Nothing decides *when* each tool is needed.**
   - Each one is invoked at the agent's or human's discretion.
   - UI Skills' routing is prose for the LLM to follow (`ui-skills-root/SKILL.md`, V).
   - UI/UX Pro Max needs the agent to name the domain and stack (V).
   - Chisel's context is a free-text FTS query (V).
   - Nothing looks at the diff and says "this change touches auth, so run the security scan; it touches
     no UI, so skip the browser check".
2. **There is no shared verdict vocabulary.**
   - SkillSpector says SAFE / CAUTION / DO_NOT_INSTALL, and CAUTION exits 0 (V).
   - Reticle says yes / no / unknown / no-fault, and its exit 1 conflates failures with setup errors (V).
   - Playwright reports stats with no UNKNOWN concept (V).
   - Nothing combines these into one answer to "is this change complete?" that never turns
     "couldn't check" into "passed".
3. **There is no record of what was *not* checked and why.** Skipping a check is silent today.
4. **No tool binds results to the exact commit and tree that was judged.** Stale evidence is
   indistinguishable from fresh.
5. **Licensing and egress posture differ, and the developer has to work that out per tool:**
   Reticle's server is FSL with telemetry on by default (V), SkillSpector sends dependency names to OSV (V),
   and UI Skills is network-only (V).

Installing all five gives an agent five capabilities, but no plan for when to use each and no reliable
verdict at the end.

## Q2. What do AgentProof, AgentSmith, AgentWard, AgentRig and DoneProof (plus Preloop) already solve?

| Project | Solves | Does not solve |
|---|---|---|
| AgentSmith | Repo-level gate inventory with reasons and `not-applicable` states. Running every configured gate. Hashed receipts. Handoffs. Multi-agent install. Anti-tamper in autonomous mode | Per-change selection (no diff or task input to planning). Skipped-gate reasons tied to the change. Per-change context and skill selection. UNKNOWN / WAIVED states |
| AgentProof | Before/after (BASE vs HEAD) test execution. Proof tests. A tamper rule taxonomy. Signed receipts | Choosing gates. Anything beyond one test command. Context and skills |
| Preloop | Path-glob → required-command selection. Fail-closed publication tied to commit and tree. A trusted profile kept outside the repo | Profile inference (it is written by hand). Content-level signals. Typed providers and UNKNOWN. Skipped-check reasons. Planning before work. Use outside its server platform |
| DoneProof | Honest receipt shape. Forbids a "done" status | Checking anything: everything is self-reported |
| AgentWard | MCP tool-call enforcement and audit | Built-in agent tools. Code changes. It is also dormant and BUSL |
| doidor/agentrig | A minimal harness contract (spec) | Tooling (its CLI was removed) |
| regenrek/agentrig | Skill and MCP distribution with trust and provenance | Per-change decisions |

## Q3. Where would RepoMarshal duplicate them?

These are the places RepoMarshal would duplicate existing tools. The decision for each:

- **Receipts and evidence formats** (AgentProof, DoneProof, AgentSmith). Keep RepoMarshal's evidence
  record minimal and bound to the plan. **Export** to DoneProof and SARIF rather than compete on receipt
  formats.
- **Test-tamper detection** (AgentProof). Detect only cheap diff-level red flags as *planning signals*
  that raise risk and add gates. Delegate before/after proof to AgentProof as an optional gate. Do not
  reimplement AP-rule-style AST analysis.
- **Path-based check selection** (Preloop, Reticle `affected`, Nx/Jest/Playwright `--only-changed`). The
  rule mechanism is not novel and must not be marketed as such. Test-level selection is delegated.
- **Harness installation, rules and agent config** (AgentSmith, AgentRig). **Not built.** RepoMarshal
  reads `AGENTS.md ## Commands`, `.agents/rules` globs and AgentSmith `verify.conf` as *inputs*.
- **Scanning, runtime perception, design knowledge** (SkillSpector, Reticle, UI/UX Pro Max). Providers
  only.

## Q4. What is the smallest truly novel layer?

The novelty is a combination of five things. None of them is a breakthrough on its own, but no
inspected project has all of them:

1. **Plans with no configuration.** The gate inventory is *detected* from the repository (manifests,
   tool configs, CI), and a built-in, versioned catalogue of **change signals** maps diffs to required
   gates. A repository gets a useful, explained plan before anyone writes a profile.
   - Preloop needs a hand-written profile.
   - AgentSmith infers an inventory but not a per-change plan.
2. **Signals from content, not only paths.** Path globs are too coarse. The signal catalogue also
   inspects:
   - dependency additions in manifests and lockfiles;
   - security-sensitive identifiers in added lines (auth, token, crypto, `subprocess`, `eval`);
   - deleted or skipped tests and narrowed test discovery;
   - edits to CI, agent config (`.mcp.json`, `.claude/`, `AGENTS.md`) and RepoMarshal's own policy.

   Each signal has a stable ID, so `--explain` can cite it.
3. **Every gate decision is explained, including skips.** Preloop records why a check was *selected*.
   Nothing records why a gate was *not*. The skip reason is what makes the "minimum sufficient" claim
   checkable.
4. **One plan from before the work to the verdict.** The same plan drives context and skill
   recommendations *before* the agent edits and the required gates *after*. Every other tool covers
   only one side.
5. **A fail-closed, five-state verdict** (PASS / FAIL / UNKNOWN / BLOCKED / WAIVED) across different gate
   kinds and typed providers.
   - It is bound to commit, tree, plan and policy hashes.
   - The policy comes from the BASE commit, so a change cannot relax its own requirements.
   - The semantics adapt Reticle's OVP principle that unknown never collapses into pass or fail.

The data structure that carries all five is a small, versioned **Change Plan IR**, emitted as JSON.
Any executor can consume it: RepoMarshal's own runner, an agent through MCP, CI, or an export to a
Preloop profile.

### Unique value proposition

> Unlike AgentSmith's repository-wide verification plan or Preloop's hand-written path profiles,
> RepoMarshal compiles each git diff — through a versioned catalogue of deterministic change signals and the
> repository's auto-detected toolchain — into an explainable Change Plan that names the context, skills and
> gates this specific change requires and why every other gate was skipped, then folds the provider results
> into a fail-closed PASS / FAIL / UNKNOWN / BLOCKED verdict bound to the exact commit, tree and BASE-commit
> policy.

**Concrete mechanism:** diff → signals (with IDs) → plan IR → gate execution → verdict bound to hashes.

## Q5. Why would a developer install RepoMarshal instead of a competitor?

The honest answer is that it should be installed *alongside* them. It replaces none of them:

- **For a full harness**, AgentSmith is the better choice. RepoMarshal can sit inside it as the per-change planner.
- **For before/after proof of a fix**, use AgentProof. RepoMarshal selects it when tests or CI change.
- **For an agent platform with container isolation**, use Preloop. A RepoMarshal plan can be exported as its profile.

RepoMarshal earns its install on these properties:

- **It works on day one, offline, with any MCP-capable agent.** Running `repomarshal plan` on an
  unconfigured repo already produces an explained plan.
- **It saves the cost of the agent's inner loop.** For small changes it skips the expensive gates
  (browser, integration, deep scans), and the skip reason is visible. Cheap gates always run.
  **CI remains the authority**: RepoMarshal targets the agent's local iterate-and-claim-done loop,
  where running everything on every edit is too slow and running nothing is unsafe.
- **The agent doesn't get to decide what "done" means.** A Claude Code `Stop` hook (and its equivalents
  in other agents) can ask RepoMarshal for the verdict, and UNKNOWN blocks the claim.

**What would make this moot:** AgentSmith adding a diff input to `verify discover`, or Preloop extracting
its selector into a standalone library. In either case RepoMarshal's lasting value is the signal
catalogue's quality, the safety of its escalation rules, and an open plan/verdict schema.
RepoMarshal therefore has to be built as an **embeddable engine with a stable schema**, not a walled harness.

## Q6. Why would maintainers of existing tools want RepoMarshal integration rather than see a hostile clone?

- **Better-targeted invocations.** A provider is called only when the plan says its domain is
  affected, so it runs less often on irrelevant changes, which means fewer noisy CAUTION results and
  fewer pointless browser runs.
- **No redistribution, no copied code, credited by name.** Providers are user-installed tools behind
  subprocess adapters. FSL and BUSL components (Reticle server, Chisel, AgentWard) are never bundled.
- **Honest status reporting.** Each integration is shown as SUPPORTED, EXPERIMENTAL, PLANNED or
  UNAVAILABLE. A provider that isn't installed produces UNKNOWN, never a fake pass.
- **Upstream contributions instead of forks.** Research found concrete small improvements to propose:
  - SkillSpector: a top-level `schema_version`, an opt-out for OSV egress, and a non-zero exit option for CAUTION.
  - Reticle: separate exit codes for "failed" and "could not run" in `verify`.
  - Playwright: structured output from the MCP `test_run` tool.
  - AgentSmith: a published verification-plan schema.
- **Interop exports** (Preloop profile, DoneProof receipt, SARIF) make RepoMarshal a *producer* for
  their ecosystems.

---

## Challenges to the premise, and resulting design changes

| MISSION.md assumption | Finding | Change |
|---|---|---|
| The planner is the unique innovation | Selecting gates by path is prior art (Preloop) | Claim the combination (inferred inventory, content signals, skip explanations, before-and-after plan, typed verdict), not path selection |
| "Minimum sufficient" process | AgentSmith R5 argues for always running everything. Minimising can miss regressions | Minimise only expensive gates. Cheap gates always run. Escalate on shared, build, CI or lockfile changes. Uncovered files get a conservative default. CI keeps running the full suite |
| `repomarshal.yaml` in the repo is the policy | An agent can edit it in the very change being judged | Load policy from BASE. Policy edits in the diff are a high-risk signal that cannot relax the current plan |
| Evidence ledger | Evidence produced by the agent is a claim (Preloop, AgentProof) | Only RepoMarshal-executed gates count as evidence. Records carry commit, tree, plan and policy hashes and tool versions |
| PASS / FAIL / UNKNOWN / BLOCKED | OVP shows that "nothing to prove" is a distinct outcome | Use per-gate `NOT_APPLICABLE` (skipped, with a reason) and `WAIVED` (human, with a reason). Neither ever counts as PASS evidence |
| `AgentAdapter` per agent | Host-specific config generation is unstable (both AgentRigs pivoted in 2026) | MCP stdio server plus CLI with the same JSON. Thin hook shims only (Claude Code `Stop` first). Print config snippets; never own host files |
| SkillSpector, Reticle, Chisel adapters in v0.1 | Reticle needs app instrumentation, and Chisel is FSL with read side effects | v0.1 has real adapters only for SkillSpector (security) and Playwright (runtime). Reticle and Chisel are PLANNED, not stubbed |
| Context engine with token savings | Without measurement, savings claims are fiction | Deterministic heuristic, labelled as such. Token counts are estimates and labelled as estimates. Correctness (always include tests and docs adjacent to the change) wins over savings |

## Decision

Proceed to Phase 2 with RepoMarshal positioned as a **local-first change-plan compiler and completion
evaluator** for coding agents.

- **Native:** analysis, signals, plan IR, gate execution, verdict.
- **Delegated:** scanning, runtime perception, design knowledge, test-level selection, enforcement, and
  agent configuration.

If Phase 6 shows that zero-config plans on real repositories are noisy or wrong, the project should
shrink to a signal catalogue plus a plan schema that other harnesses consume.
