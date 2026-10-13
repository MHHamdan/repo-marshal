# Product definition

_Phase 2 (MISSION.md §63). This document follows from the decision in
[`research/gap-analysis.md`](research/gap-analysis.md). The architecture, ADRs and roadmap must stay
consistent with it._

## One sentence

> RepoMarshal compiles a code change into an explained Change Plan of the context, skills and checks
> it needs, then runs those checks and returns a verdict that never counts "couldn't check" as
> "passed".

## Product category

RepoMarshal is a **local-first change-plan compiler and completion evaluator for AI coding agents**.
It is a library-like engine with a CLI and an MCP server. It is not a harness, not an agent and not
a CI service.

It has two halves, and they share one data structure, the **Change Plan**:

| Half | Question it answers | When |
|---|---|---|
| **Planner** | "What does *this* change need?" Context, skills and the gates that must pass, plus why each other gate is skipped | Before or while the agent works |
| **Evaluator** | "Is this change done?" Runs the planned gates and returns PASS, FAIL, UNKNOWN or BLOCKED | When the agent (or a person) claims completion |

"Engineering control plane" (MISSION.md §1) is the aspiration. The product sold in v0.1 is the
compiler plus the evaluator, which is the part no inspected project provides
(gap analysis, Q4).

## Primary user

**Primary: a developer or tech lead running AI coding agents on a real repository.** The agent may be
Claude Code, Codex, Cursor, Copilot or Gemini CLI. The repository has a test suite slow enough to
hurt and several domains (frontend, backend, auth, dependencies, CI). This user has two problems,
often at once:

1. **Over-checking.** The agent, or its harness rules, runs the full suite, browser checks and scans
   on every iteration, even for a README edit. That burns minutes and tokens, so people turn the
   checks off.
2. **Under-checking.** The agent says "done" after running whatever it chose to run. A change to auth
   or dependencies gets the same scrutiny as a typo, and nobody can say afterwards what was checked.

**Secondary: builders of harnesses and platforms** (for example AgentSmith, Preloop, internal agent
platforms). They want a per-change plan and a verdict schema they can embed, without writing a
selector.

**Not the target for v0.1:**
- Tiny repositories where the full suite runs in seconds. Planning saves nothing there.
- Teams that need *enforcement* (blocking tool calls, sandboxing). RepoMarshal advises and
  evaluates, and can block a completion claim. It does not contain or confine the agent.

## Triggers

| Trigger | Who | Mechanism |
|---|---|---|
| Starting a task | The agent | Calls the MCP tool `plan` (or `repomarshal plan "<task>" --json`) and gets context, skills and the gates it will be held to |
| Claiming done | The agent's host | A stop or completion hook (Claude Code `Stop` first) runs `repomarshal done`. A non-PASS verdict sends the agent back with the failing or unknown gates |
| Reviewing a branch | A person | `repomarshal plan --explain` shows why each gate is required or skipped. `repomarshal evidence` shows what actually ran |
| Pull request (later) | CI | Runs the same plan from the BASE policy and publishes the verdict. CI remains authoritative for merge |

## Input

All inputs are local. Network access is never needed for planning.

| Input | Trust | Use |
|---|---|---|
| Git state: BASE (merge-base, or an explicit `--base`), HEAD, staged and unstaged changes, untracked files | Observed fact | Changed files, content diffs, renames, deletions |
| Repository metadata: manifests, lockfiles, tool configs, CI files, `AGENTS.md ## Commands`, skill directories | Read **from BASE** where it defines policy or commands | Toolchain and gate inventory, stack detection, local skill inventory |
| `repomarshal.yaml` policy (optional) | **Read from BASE only** | Overrides, extra rules, waiver policy. If the diff edits this file, the edit is a high-risk signal and cannot relax the current plan |
| Task description (optional free text) | **Untrusted** | **Can only add requirements, never remove them.** "This is just a docs change" cannot skip a gate that the diff requires |
| Provider availability (SkillSpector, Playwright, …) | Observed | Gates whose provider is missing become UNAVAILABLE, which counts as UNKNOWN, never PASS |

Two consequences follow:
- **The diff dominates.** With no task text, RepoMarshal still produces a full plan from the diff.
- **Before any edits exist**, the plan is a *forecast* from the task text and repo metadata, and is
  labelled as one. The binding plan is recompiled from the actual diff at completion time.

## Decision

For each change, RepoMarshal decides the following. Every decision carries a machine-readable reason
that cites the signal IDs and files behind it.

1. **Change signals.** Which deterministic signals fire. Examples:
   - `dependency.manifest_changed`
   - `security.auth_path`
   - `tests.deleted`
   - `ci.workflow_changed`
   - `policy.self_modified`
   - `docs.only`
2. **Domains and risk.** Domains such as docs, frontend, backend, auth/security, dependencies, CI,
   infra and agent-config. A risk level of `low`, `standard` or `elevated`, chosen by the
   highest-risk signal rather than an average.
3. **Context.** A ranked set of files the agent should read: changed files, their tests, nearby
   modules, and relevant docs and ADRs. Sizes are estimated. **The ranking is a deterministic
   heuristic and is labelled as such.** Correctness rules, such as always including the tests of
   changed code, outrank the size budget.
4. **Skills.** Zero to three skills from the repository's local inventory (SKILL.md frontmatter),
   matched to the domains and stack. If nothing matches confidently, the answer is "none".
   External skills are flagged for a security audit before use.
5. **Gates.** Each candidate gate (docs check, lint, typecheck, unit tests, integration tests,
   dependency audit, secrets scan, skill audit, runtime/browser check, accessibility) is
   **REQUIRED**, **SKIPPED** (with a reason) or **UNAVAILABLE** (required, but no runnable command or
   provider).
6. **Evidence level.** `standard` or `elevated`. Elevated requires every required gate to have
   RepoMarshal-executed evidence and allows no waivers without an explicit policy.

Safety rules that hold the "minimum" in "minimum sufficient" in check:
- **Cheap gates are not minimised.** Lint, typecheck and format run whenever the stack has them and
  code changed.
- **Expensive gates are selected per change**: integration tests, browser/runtime checks and deep
  scans.
- **Shared configuration widens the plan.** Changes to build config, CI, lockfiles, or files no
  signal covers trigger a conservative default, never a smaller plan.
- **CI should still run the full suite.** RepoMarshal optimises the agent's inner loop; it does not
  replace CI.

## Output

1. **A Change Plan.** Versioned JSON (`schema_version`) with signals, domains, risk, context, skills,
   gates and reasons. The human view is `--explain`. The plan has a content hash, so the evaluator
   can tell which plan it is judging.
2. **Evidence records.** Written under `.repomarshal/evidence/`, one per evaluation. Each holds:
   - commit, tree hash, plan hash and policy hash;
   - for every gate: the command or provider, exit code, duration, tool version, and digests of
     stdout and stderr (redacted);
   - waivers, with their reasons.

   Only gates RepoMarshal ran count as evidence. Agent-reported results are recorded as *claims*.
3. **A completion verdict.** One of four states (MISSION.md §53):

| Verdict | Meaning | Exit code |
|---|---|---|
| **PASS** | Every REQUIRED gate passed, with evidence for the current commit, tree and plan | 0 |
| **FAIL** | At least one REQUIRED gate produced negative evidence | 1 |
| **UNKNOWN** | No gate failed, but at least one REQUIRED gate could not produce reliable evidence (provider unavailable, incomplete scan, nothing observable) | 2 |
| **BLOCKED** | Policy prevents evaluation or completion: an invalid or self-modified policy, or a waiver that policy forbids | 3 |

The precedence is BLOCKED > FAIL > UNKNOWN > PASS. Gate-level states are separate:
- SKIPPED and NOT_APPLICABLE mean the plan decided the gate is not needed. They are not evidence.
- WAIVED means a person accepted the gate with a reason. Whether that permits PASS is a policy
  decision, and the waiver is always reported.

None of these states can be turned into PASS without evidence.

4. **Interop exports (later):** SARIF, a DoneProof receipt and a Preloop verification profile.

## Value

Why RepoMarshal is substantially better than installing the tools individually:

| Without RepoMarshal | With RepoMarshal |
|---|---|
| The agent decides what to run, so either everything runs or not enough does | The diff decides, and every skip has a stated reason a reviewer can challenge |
| SkillSpector, Playwright and test runners each speak their own pass/fail dialect, and "CAUTION", "skipped" or "no tests found" quietly read as success | One four-state verdict where missing evidence is UNKNOWN |
| "Done" is the agent's claim | "Done" is a verdict tied to a commit, tree, plan and policy hash, from checks RepoMarshal ran |
| The policy lives where the agent can edit it | The policy is read from BASE, and policy edits escalate the plan |
| Each tool needs per-agent configuration | One MCP server and CLI work with any agent. Agent configuration is printed, not owned |
| Value starts only after authoring a profile (Preloop) or adopting a harness (AgentSmith) | `repomarshal plan` gives an explained plan on an unconfigured repository |

How we will measure the value (MISSION.md §43; no numbers are claimed until measured):
- **Planner precision on fixtures:** required gates selected (target: all) and expensive gates
  avoided for docs-only and narrow changes.
- **Verdict honesty:** broken fixtures FAIL, and unobservable or missing-provider cases are UNKNOWN,
  never PASS.
- **Context efficiency:** selected versus candidate context size, labelled as an estimate when not
  tokenizer-measured.
- **Latency:** cold and warm planning time on the fixtures and on this repository.

## Non-goals

RepoMarshal explicitly refuses to become:

- **A coding agent, an IDE, or an autonomous maintainer.** It never edits source files.
- **A harness or rules installer.** That is AgentSmith's and AgentRig's job. RepoMarshal *reads*
  `AGENTS.md`, rules and verify configs as inputs.
- **An agent-config generator.** It does not own `CLAUDE.md`, `.cursor/rules` and similar files. It
  can print snippets.
- **A security scanner.** SkillSpector and others are providers. RepoMarshal's own signals are
  planning heuristics, not vulnerability findings.
- **A sandbox or runtime policy enforcer.** That belongs to AgentWard, Preloop, MCP gateways and host
  permission systems. Gates run with the user's permissions in the user's environment, and the
  documentation says so.
- **A browser automation engine or runtime perception layer.** That belongs to Playwright and Reticle.
- **A design-knowledge base or skill registry.** That belongs to UI/UX Pro Max, UI Skills,
  regenrek/agentrig and skills.sh.
- **A test-impact analyser.** Selecting individual tests is delegated to Jest, Playwright, Nx and
  pytest plugins. RepoMarshal selects *gates*.
- **A replacement for CI.** CI keeps running the full suite and remains the merge authority.
- **An LLM-judged system.** v0.1 is fully deterministic. Any future model-assisted classification
  must be opt-in, labelled, and able to add requirements only, never remove them.
- **A cloud service.** No telemetry, and no upload of repository content.

## v0.1 primary workflow

```text
repomarshal init           # writes a minimal repomarshal.yaml (optional), touches nothing else
repomarshal doctor         # detected stack, gates, providers, config validity
repomarshal plan "<task>"  # explained Change Plan (--json, --explain)
repomarshal done           # recompile the plan from the diff, run required gates, record evidence, verdict
```

Plus `repomarshal mcp`, which exposes `plan` and `done`/`status` to any MCP-capable agent.

The scope tiers (MUST, SHOULD, MAY, NOT NOW) are set in `docs/roadmap.md` (Phase 5).
