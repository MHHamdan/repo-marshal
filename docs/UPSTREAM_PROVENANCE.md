# Upstream provenance

This file lists every third-party project RepoMarshal has inspected or integrates with.

- **Inspection:** read-only, from temporary clones. **No upstream code has been copied into
  RepoMarshal.** If that ever changes, this file must name the file, its source path and SHA, and the
  license obligations that apply before the copy is committed.
- **Date inspected:** 2026-10-13 for all entries.
- **Evidence:** in [`research/notes/`](research/notes/).
- **Not inspected:** GitHub issues and releases pages. The GitHub API was unreachable.

Integration methods, in order of preference (MISSION.md §32):
1. documented protocol or API;
2. MCP;
3. CLI or subprocess adapter;
4. plugin or provider interface;
5. independent clean implementation of ideas;
6. code reuse.

## Primary upstream sources

### NVIDIA/SkillSpector
- **Commit:** `69d7d059da38160296b2311320ed4c72a9c3fdb6` (main, 2026-10-12) was inspected in Phase 1. **Correction (2026-10-14):** tag v2.12.0 is at `c7958a3`, 630 commits earlier; main also reports 2.12.0. The v0.3 adapter is pinned to and tested against the v2.12.0 tag (`c7958a3`), range `>=2.12,<2.13`.
- **License:** Apache-2.0 (sole `LICENSE`; SPDX headers in source). No NOTICE file. Contributions under DCO. Bundled YARA rule provenance is UNKNOWN.
- **Studied:** CLI and exit codes, JSON/SARIF contracts, MCP `scan_skill`, offline behaviour (run locally), dependency footprint.
- **Integration:** CLI subprocess adapter (`scan --no-llm --format json`). Optional, user-installed, never a pip dependency.
- **Code reused:** none.
- **Obligations:** none for subprocess use. Credit as "NVIDIA SkillSpector (Apache-2.0)" without implying endorsement.
- **Concerns:** no schema version in JSON; Python ≥3.12; OSV network calls even with `--no-llm`; fast release cadence.

### reticlehq/reticle
- **Commit:** `1a337fa254466c33fb5888ddc0e269e1811eb2f9` (2026-10-13; latest tag v3.6.0)
- **License per component:**
  - `open-verification/`, `core/`, `engine/`, `adapters/**`: **Apache-2.0**, with NOTICE "Copyright 2026 Reticle Labs".
  - `server/` (the `reticle` CLI), `init/`, `spec-runner/`: **FSL-1.1-ALv2**.
  - `server/src/features/ee/`: **Reticle Enterprise License** (source-available, paid key).
- **Studied:** OVP verdict semantics, flow format, CLI `verify`/`gate`/`affected`, MCP tools, telemetry, licensing.
- **Integration:** PLANNED optional subprocess adapter when `.reticle/` exists, with telemetry disabled. Verdict ideas from the Apache-2.0 OVP spec are reimplemented independently.
- **Code reused:** none.
- **Obligations:**
  - Credit "Open Verification Protocol (Reticle, Apache-2.0)" where its semantics influenced ours.
  - Never bundle or redistribute FSL or enterprise components.
- **Concerns:** needs app instrumentation; `verify` exit 1 is overloaded; enterprise "verify-before-merge gates" are on Reticle's roadmap.

### chisel-sh/chisel
- **Commit:** `f5a2cc470a240d6ad521be5006d117ef252d581b` (2026-09-09; v0.2.3)
- **License:** **FSL-1.1-Apache-2.0** (LICENSE.md, Copyright 2026 Chisel Team).
  - **Conversion:** each release converts to Apache-2.0 on its second anniversary, so v0.2.3 converts on about 2028-09-09.
  - **Competing Use:** offering it in a commercial product or service that substitutes for the Software or other Licensor offerings, or as a hosted service.
  - **History:** releases v0.1.0–v0.1.6 also offered `packages/` under Apache-2.0. That line was removed on 2026-02-18.
- **Studied:** spec/doc file formats, CLI, side effects, license.
- **Integration:** PLANNED optional reader of `.chisel/specs/*.md` frontmatter, using RepoMarshal's own parser. Optional subprocess only behind an explicit flag, because reads can mutate the repo.
- **Code reused:** none. No vendoring, linking or bundling.
- **Obligations:** none while no Chisel code is distributed. Use the "Chisel" name only nominatively.
- **Concerns:** FSL is not OSI-approved; the format is churning; docs and code disagree on frontmatter.

### ibelick/ui-skills
- **Commit:** `f2264c30213a8a55bd0a6a7668d0438e4564e090` (2026-10-12; latest tag v0.2.3)
- **License:** MIT (Copyright 2026 Julien Thibeaut). The 395 catalogued third-party skills carry their own licenses, which are not tracked per entry.
- **Studied:** progressive-disclosure CLI, remote MCP (`list_skills`, `get_skill`), skill frontmatter, routing (prose only).
- **Integration:** PLANNED optional remote catalog behind a network opt-in. The ≤3-skill routing idea is generalised in our own code.
- **Code reused:** none.
- **Obligations:** none unless code or first-party skills are copied (then the MIT notice applies). Third-party skill content is never bundled.
- **Concerns:** network-only; skill content is unpinned.

### nextlevelbuilder/ui-ux-pro-max-skill
- **Commit:** `7246805992a42b7bba37d4cac83b01416f0c6c44` (2026-10-12; latest tag v2.15.0)
- **License:** MIT (Copyright 2024 Next Level Builder). Exception: `.claude/skills/ui-styling/` ships an Apache-2.0 LICENSE.txt while its frontmatter says MIT, and it bundles fonts under SIL OFL 1.1.
- **Studied:** data corpus, BM25 search with abstention, stack handling, objective vs subjective content.
- **Integration:** PLANNED optional advisory provider via `scripts/search.py --json`.
- **Code reused:** none. No data vendored.
- **Obligations:** MIT notice if any data is ever copied. Exclude `ui-styling/` assets.
- **Concerns:** license conflict in `ui-styling`; most of the data is subjective.

## Competitive prior art

### GenRamzi/AgentProof
- **Commit:** `7a748e9820bc15e4182aee92c16be5160e2be7e4` (2026-08-15; tags v0.2.0rc1/rc2)
- **License:** Apache-2.0. No NOTICE file.
- **Studied:** BASE/HEAD worktree execution, proof tests, tamper rules AP001–AP502, receipt and policy schemas.
- **Integration:** PLANNED optional "test-integrity" gate provider (CLI, receipt ingestion). Its rule taxonomy is referenced, not copied.
- **Code reused:** none.
- **Obligations:** none for subprocess use.

### agentward-ai/agentward
- **Commit:** `0e049066a8f4b48196803e330bc2f0a54bb2eda4` (2026-04-28; v0.5.2)
- **License:** **BUSL-1.1** since 2026-04-24 (Change Date 2028-04-24, then Apache-2.0). Earlier commits are Apache-2.0.
- **Studied:** MCP proxy enforcement, policy schema, audit log.
- **Integration:** none. Runtime enforcement is out of scope. A possible future export is plan scope written as AgentWard policy data.
- **Code reused:** none. Never embed it.

### PromptPartner/agentsmith
- **Commit:** `0901316e730c4aca7a2aabe1562c4d63b1ea0444` (2026-10-12; latest tag v0.3.1)
- **License:** MIT (Copyright 2026 PromptPartner).
- **Studied:** `verify discover` verification-plan-v1, receipts, profiles and rules, autonomous-run anti-tamper, agent registry.
- **Integration:** PLANNED reading of `.harness/verify.conf` as a gate-inventory source.
- **Code reused:** none.

### doidor/agentrig
- **Commit:** `05d0dd5e8c8276faa57d786080dc3b18c6c74d7b` (2026-10-02; spec 2.0; last CLI release v0.12.0)
- **License:** MIT (Copyright 2026 Microsoft Corporation).
- **Studied:** AGENT-RIG.md contract, historical projection map, eval design.
- **Integration:** read `AGENTS.md ## Commands` and `.agents/rules` frontmatter as policy input (format-level interop).
- **Code reused:** none.

### regenrek/agentrig
- **Commit:** `50b6983d2f6264511062be4f1b8877615f79639b` (2026-08-13; v2.0.2)
- **License:** MIT.
- **Studied:** Agent Plugins 1.0 package layout, registry trust model, provider materialisation.
- **Integration:** PLANNED distribution of RepoMarshal as an Agent Plugins package (SKILL.md plus stdio `mcp.json`).
- **Code reused:** none.

### giugiu-a11y/doneproof
- **Commit:** `4a45ebc01fb41c33130657e8addd15f4c9bcc4d4` (2026-08-23; v0.5.0)
- **License:** MIT.
- **Studied:** receipt schema and completion-claim policy.
- **Integration:** PLANNED export of a DoneProof receipt.
- **Code reused:** none.

### preloop/preloop
- **Commit:** `71dd091e8e6e7503a8105b3b18b772df9707e15c` (HEAD, 2026-10-13). Feature merge `c762ff2a35eb7ea8971b1ed7a40235575ea49da2` (PR #435, 2026-09-06).
- **License:** Apache-2.0.
- **Studied:** `VerificationProfile` selection (`always` / `rules` / `unknown_default`), fail-closed evidence evaluation, trusted-profile boundary.
- **Integration:** PLANNED export of a plan as a Preloop profile. The ideas are reimplemented independently.
- **Code reused:** none.

## Foundational references

### modelcontextprotocol/modelcontextprotocol
- **Commit:** `3778656b2e0ba6725035e816fe3085374cbaa736` (2026-10-12). Latest released spec is 2026-07-28 (tag `2026-07-28` = `5f5440bb`).
- **License:** transitioning from MIT to Apache-2.0 for code and specs; docs are CC-BY-4.0.
- **Studied:** tools and structured output, annotations, stateless 2026-07-28 changes, deprecations, extensions (Tasks, Skills).
- **Integration:** RepoMarshal implements an MCP server via an SDK. The spec is the protocol contract.
- **Code reused:** none.

### microsoft/playwright
- **v0.3 adapter:** `@playwright/test` 1.63.0 (npm, released 2026-09-04), JSON reporter; tested range `>=1.50,<1.65`.
- **Commit:** `a3401cfe0dcf317101572d025fa46248d0779b42` (2026-10-12; package `1.65.0-next`). Shallow, sparse clone, so tags and history were not inspected.
- **License:** Apache-2.0. NOTICE: Copyright (c) Microsoft Corporation, includes Puppeteer-derived code.
- **Studied:** JSON reporter types (`types/testReporter.d.ts`), exit codes, `--only-changed`, MCP and agent tooling.
- **Integration:** subprocess adapter using `--reporter=json`. Report fields are parsed from our own minimal type description.
- **Code reused:** none.
- **Obligations:** none for subprocess use. Cite `testReporter.d.ts` as the interface source.

## Discovered via web search (not cloned; no SHA recorded)

GuardSpine Code Action (BSL 1.1), agent-pr-gate (MIT), agent-pr-reviewer-lite (MIT), groundtruth (MIT),
cngx (MIT), verify-mcp-server (MIT), GUILDLESS (MIT), loop-hooks (MIT), chiruu12/marshal (MIT).

- **Studied:** ideas only, from public pages. No integration; no code reused.
- **Before relying on any of them:** clone it and record its SHA here.
