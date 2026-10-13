# Prior art: context / skills repos (Chisel, UI Skills, UI/UX Pro Max)

Research date: 2026-10-13. Method: read-only inspection of local clones under
`<research-clones>/` (actual dir names use a single underscore: `chisel-sh_chisel`,
`ibelick_ui-skills`, `nextlevelbuilder_ui-ux-pro-max-skill`). No code copied. GitHub web/API
not reachable: **issues, PRs, releases pages, npm/crates registry metadata were NOT inspected**.
Only one upstream script was executed (UUPM `search.py`, read-only queries, no `--persist`);
the `__pycache__/` it created was removed afterward; all three clones are `git status` clean.

Labels: **VERIFIED** (path:line cited, paths relative to each repo root), **INFERRED**, **UNKNOWN**.

---

## 1. Chisel (`chisel-sh/chisel`)

| Field | Value |
|---|---|
| Identifier | github.com/chisel-sh/chisel; crate/bin `chisel` (packages/cli/Cargo.toml) |
| SHA | `f5a2cc470a240d6ad521be5006d117ef252d581b` |
| Last commit | 2026-09-09T12:28:36-05:00 "chore: release v0.2.3 (#32)" |
| History | 49 commits, first `aae0a92` 2026-02-16; not shallow |
| Tags | chisel-v0.1.0 (2026-02-16) … chisel-v0.1.8 (2026-02-20), v0.2.0/v0.2.1 (2026-03-29), v0.2.2 (2026-08-19), v0.2.3 (2026-09-09). Note: log contains "chore: release v0.3.0 (#25)" (3d90696) but no v0.3.0 tag; workspace version is 0.2.3 (Cargo.toml:8) |
| Language | Rust 2021 workspace (clap, ratatui TUI, sqlx/SQLite FTS5, serde_yaml, reqwest) + Astro landing/docs site |
| License | FSL-1.1-Apache-2.0 (all components, currently) |

### 1.1 What it is
- "The project brain for founders building with AI agents… local-first knowledge base… Specs for
  active work. Docs for permanent knowledge." VERIFIED README.md:3-5.
- Features: lifecycle specs draft→ready→in-progress→shipped→archived; Markdown-first, Git-backed docs;
  Human vs Machine (YAML) output; local-first. VERIFIED README.md:9-13.
- No MCP server, no library API intended for external use (crates are `publish = false`,
  Cargo.toml:13; only the `chisel` bin sets `publish = true`, packages/cli/Cargo.toml:9). No
  `mcp` string anywhere in source/docs. VERIFIED (grep). Integration surface = CLI + on-disk files.

### 1.2 License (current text) — VERIFIED
- LICENSE.md:3 "Chisel is distributed under the **Functional Source License, Version 1.1** (FSL-1.1-Apache-2.0)."
- LICENSE.md:9 "Copyright 2026 Chisel Team." Cargo.toml:11 and package.json:17 `FSL-1.1-Apache-2.0`;
  every crate uses `license.workspace = true` (packages/*/Cargo.toml:6). No SPDX/source headers in `.rs`
  files; no NOTICE; no CLA (CONTRIBUTING.md is a short process note, no CLA/DCO).
- **Competing Use** (LICENSE.md:19-22), verbatim: "A Competing Use means making the Software available
  to others in a commercial product or service that: 1. substitutes for the Software; 2. substitutes for
  any other product or service we offer using the Software that exists as of the date we make the
  Software available; or 3. is offered to third parties as a hosted or managed service, where the
  Software is a material part of the service."
- **Conversion** (LICENSE.md:33): "irrevocably grant you an additional license to use the Software under
  the Apache License, Version 2.0 that is effective on the second anniversary of the date we make the
  Software available." No fixed Change Date: it is per released version. INFERRED dates:
  v0.1.0 → 2028-02-16; v0.2.3 (HEAD) → 2028-09-09.
- Redistribution conditions: retain notices, include terms, don't alter proprietary notices
  (LICENSE.md:26-29). Trademarks: no rights except identifying origin (LICENSE.md:31). Patent grant
  limited to the Software alone (LICENSE.md:24).
- **License history**: `git log --follow LICENSE*` → `aae0a92` (initial, 2026-02-16) and `2bdf235`
  (2026-02-18, "docs: unify FSL licensing across project") which deleted the line "The libraries in
  `packages/` are additionally available under the **Apache License, Version 2.0**." VERIFIED via
  `git show 2bdf235`. Tags chisel-v0.1.0 … v0.1.6 still carry that line (LICENSE.md:5 at those tags,
  VERIFIED); per-crate `license = "Apache-2.0"` was replaced by workspace FSL in `ba3e4e7`
  (2026-02-16). INFERRED: the `packages/` library code as published at v0.1.0–v0.1.6 is additionally
  Apache-2.0 (licenses are not retroactively revocable), but it is old (issues-based, pre-specs) and
  of little value; do not rely on it without counsel.
- Marketing text: "Free for non-competing use and converts to Apache 2.0" (apps/landing/src/pages/index.astro:503-504).

### 1.3 Would RepoMarshal be a "Competing Use"? — INFERRED (not legal advice)
- Prong 1 (substitutes for the Software): RepoMarshal is a change-aware planner/verifier, not a
  docs/spec knowledge base. As long as it does not ship its own spec-lifecycle store + docs KB +
  `context create` equivalent as a replacement, it does not substitute. **Low risk**, but it rises if
  RepoMarshal grows a "specs/active-work lifecycle" or "project knowledge context generation" feature
  that overlaps Chisel's core. Note RepoMarshal's "context" planning partially overlaps Chisel's
  `context create` (FTS context blob) — keep that a planner over *existing* repo sources, not a KB.
- Prong 2 (substitutes for other Licensor products existing at release date): UNKNOWN which other
  products chisel.build offers (site not inspected).
- Prong 3 (hosted service with the Software as material part): N/A for local-first CLI.
- Crucially, all three prongs require "making the **Software** available to others in a commercial
  product". If RepoMarshal does not bundle/redistribute Chisel code (no vendoring, no linking of its
  crates), FSL obligations are not triggered at all. RepoMarshal is open-source; whether it is
  "commercial" is itself unclear — another reason to not include Chisel code.
- **Safe integration forms** (INFERRED):
  1. Reading `.chisel/specs/*.md` / `.chisel/docs/**/*.md` / `chisel.toml` with RepoMarshal's own
     parser = interoperability with a documented file format (format documented in
     apps/landing/src/content/docs/docs/reference/specs.md and config.md). File formats (YAML
     frontmatter + Markdown) are not the "Software"; independently written parser, no code copied. Safest.
  2. Invoking a user-installed `chisel` binary as a subprocess (optional adapter): RepoMarshal neither
     copies nor distributes Chisel → no FSL obligations. Safe, but see side effects below.
  3. **Avoid**: vendoring/porting Chisel Rust code, depending on its crates, bundling the binary in
     RepoMarshal distributions, or using "Chisel" in RepoMarshal's name/branding (trademark clause).

### 1.4 CLI (VERIFIED packages/cli/src/main.rs)
Global: `-m, --machine` → YAML output (main.rs:38-40; render via serde_yaml, chisel-render/src/lib.rs `MachineOutput`).
Root is always `std::env::current_dir()` (main.rs:229); no `--root`/`-C` flag.

| Command | Args/flags | Notes |
|---|---|---|
| `chisel init` | `-n, --name` | creates `.chisel/docs/`, seeds docs + example spec, writes `.chisel/PROMPT.md`, appends `.chisel/index.db` to `.gitignore` (main.rs:485-539) |
| `chisel docs [overview\|list\|show\|index\|search\|fuzzy-search\|reorder\|reorder-category\|new\|edit\|move\|delete]` | list: `[path=.] -a/--all --no-ignore --hidden`; new: `[title] -c/--category`; reorder: `<path> <order>` | no subcommand or human mode → ratatui TUI (main.rs:80-162, 251-369) |
| `chisel spec [new\|list\|view\|status\|search\|edit\|delete\|index]` | new: `[title] -a/--area -t/--template (feature\|adr) -c/--content (- = stdin)`; list: `-s/--status`; status: `<slug> <status>` | main.rs:165-217, 371-469 |
| `chisel context create <query>` | — | FTS5 query → XML-ish `<context><file path=…>…</file><spec path=…>…</spec></context>` (main.rs:16-30, 470-478); top 10 docs + top 10 specs by FTS rank (chisel-store/src/lib.rs:200-244). **No XML escaping** of path/content (main.rs:24-26). Raw query goes straight into FTS5 `MATCH` → FTS syntax errors possible (INFERRED). |
| `chisel upgrade` | — | runs `curl -sL https://install.chisel.build \| sh` (upgrade.rs:107-119) |

Exit codes: anyhow errors → non-zero (INFERRED standard Rust main-returning-Result behaviour); no
documented code table.

### 1.5 On-disk layout & formats (VERIFIED)
```
chisel.toml                 # optional: [docs] source = "<path>", [specs] source = "<path>" (chisel-fs/src/config.rs:1-32)
.chisel/
  PROMPT.md                 # agent instructions generated by init (main.rs:514-519)
  index.db                  # SQLite FTS5 cache, gitignored (chisel-store/src/lib.rs:48; migrations/001-003)
  update_check.json         # upgrade-check cache (upgrade.rs:25-29)
  docs/<category>/<slug>.md # docs; _category.yaml {label, order} per folder (chisel-docs/src/lib.rs:282-285, 512)
  specs/<slug>.md           # flat; status in frontmatter (chisel-specs/src/default_source.rs; lib.rs:225-233)
```
- Spec frontmatter (chisel-specs/src/lib.rs:35-47): `title: string` (req), `status:
  draft|ready|in-progress|shipped|archived` (req, kebab-case, lib.rs:22-33), `created: date` (req,
  `NaiveDate`), `updated: date` (req), `area?: string`, `related_docs?: [string]`,
  `open_questions?: [string]`. File = `---\n<yaml>---\n\n<body>` (default_source.rs:101-105).
  Slug = file stem; slug from title via `slug::slugify` (lib.rs:262; chisel-fs/src/lib.rs `slugify_title`).
- Parsing: file must start with `---`; `splitn(3,"---")` (chisel-specs/src/parsing.rs:9-18) —
  a `---` horizontal rule inside YAML would break it (INFERRED). Unparseable specs are silently
  skipped in `list` (default_source.rs:60-66 `if let Ok(spec)`).
- Templates: feature (sections What and Why / Success Criteria / Constraints / Approach /
  Alternatives Considered / Open Questions / Implementation Notes), adr (Context / Decision /
  Consequences); default = feature (lib.rs:147-210, 274). Useful prior art for RepoMarshal's
  "success criteria" evidence mapping.
- Doc frontmatter: `title`, `created_at: DateTime<Utc>`, `tags: []`, `order?: i32`
  (chisel-docs/src/lib.rs:273-279); invalid frontmatter → treated as no frontmatter (parsing.rs:10).
- **Doc/code mismatches** (VERIFIED): reference doc says filenames `<id>_<slug>.md`
  (reference/specs.md:25) but code writes `<slug>.md` (default_source.rs resolve_path); doc says
  `created`/`updated` are ISO datetimes and `updated` optional (specs.md:33,49-50) but code requires
  both as plain dates (`NaiveDate`, no `serde(default)`) — a spec written per the docs would fail to
  parse and be silently dropped (INFERRED from serde behaviour). RepoMarshal's reader should be lenient
  (accept date or datetime, optional `updated`).
- Docs source selection: if `chisel.toml [docs].source` exists use it, **else if `src/content/docs`
  exists use it (Starlight mode)**, else `.chisel/docs` (chisel-docs/src/lib.rs:320-337).
- Repo's own `.chisel/` contains legacy `issues/` and `designs/` (pre-specs format); issues primitive
  was replaced by specs in `7689b53` (feat!), migration 002 drops `issues` tables.

### 1.6 Side effects relevant to subprocess use (VERIFIED)
- Any `docs`/`spec`/`context` command opens/creates `.chisel/index.db` (Store::new, chisel-store/src/lib.rs:47-50).
- **`SpecsService::new` auto-migrates** legacy `active/ shipped/ archived/` subdirs by `fs::rename`
  into the flat layout and deletes emptied dirs (chisel-specs/src/lib.rs:232-250;
  default_source.rs:13, 26-55). So even `chisel spec list --machine` can rewrite the working tree.
- Human mode does a GitHub API call (`api.github.com/repos/chisel-sh/chisel/releases/latest`) at most
  daily and writes `.chisel/update_check.json` (main.rs:233-242; upgrade.rs:31-66, 69). `--machine` skips it.
- Edit commands spawn `$EDITOR`/`$VISUAL`/`vim` (chisel-fs/src/lib.rs:24-27).
- ⇒ RepoMarshal must treat `chisel` invocations as non-read-only; prefer direct file reading.

### 1.7 Tests
22 Rust unit tests (`#[test]`/`#[tokio::test]` count across packages: docs 5, fs 3, render 1, specs 6,
store 5, cli 2). CI: `cargo test`, `cargo clippy -D warnings`, rustsec audit with 5 ignored advisories
(.github/workflows/ci.yml). No integration/e2e tests of CLI. VERIFIED.

### 1.8 Assessment
- Recommended integration: **optional read-only adapter that parses `.chisel/specs/*.md` (+ `chisel.toml`
  `[specs].source`) with RepoMarshal's own code**, to pull active-work context (`status: in-progress`,
  `open_questions`, `related_docs`) into plans. Optional subprocess `chisel context create` only behind
  an explicit flag, acknowledging side effects. No code reuse.
- Attribution obligations: none if no Chisel code is redistributed. Nominative mention ("reads Chisel
  spec files") is fine; avoid trademark-like usage.
- Compatibility concerns: format is young and churning (issues→specs in Mar 2026, status-dirs→flat in
  Aug 2026, auto-migration); doc/code schema mismatch; no versioned schema; FTS query syntax leakage.
- Strengths: clean simple Markdown+YAML format; explicit lifecycle; `--machine` YAML; agent PROMPT.md
  convention; spec templates with Success Criteria.
- Weaknesses: FSL (not OSI); small project (49 commits, single main author per log); mutating reads;
  no MCP; unescaped context XML; silent parse failures.

---

## 2. UI Skills (`ibelick/ui-skills`)

| Field | Value |
|---|---|
| Identifier | github.com/ibelick/ui-skills; npm `ui-skills` (bin `ui-skills`), site ui-skills.com |
| SHA | `f2264c30213a8a55bd0a6a7668d0438e4564e090` |
| Last commit | 2026-10-12T18:28:03+02:00 (merge PR #92, "Add kemiljk, arla6ka, ponytail, graphify…") |
| History | 424 commits, first `c757c7d` 2026-01-07 |
| Tags | v0.2.3 (2026-06-21), v0.2.2 (2026-06-14), v0.1.6 … v0.0.8. package.json says 0.2.4 (untagged) |
| Stack | Astro 7 site on Cloudflare (wrangler), React 19, Tailwind 4; CLI in TS run via tsx |
| License | MIT, "Copyright (c) 2026 Julien Thibeaut" (LICENSE:3); package.json:4 `MIT`; license file added `b4c5945` 2026-01-10, never changed |

### 2.1 What it is
A curated **remote registry/directory** of third-party agent skills (SKILL.md files hosted in other
GitHub repos), plus a website, CLI, and remote MCP endpoint. VERIFIED README.md:5-34, AGENTS.md:72-77
("Add skills in `src/data/registry.ts` only… Do not add local `skills/*` files unless the repo actually
hosts the skill markdown").

- Registry: **395 entries** from 100 GitHub users / 109 user-repo pairs (counted `slug:` in
  src/data/registry.ts; matches `skillCount: 395` in src/data/agent-skills-digests.json). VERIFIED.
- Locally hosted skills: **7** (`skills/{ui-skills-root, baseline-ui, create-design-md,
  fixing-accessibility, fixing-metadata, fixing-motion-performance, improve-ui}/SKILL.md`, 43,627 bytes
  total). VERIFIED.
- Topics/categories: **26** `TopicSlug`s (src/data/registry.ts:17-46). Usage counts: frontend 259,
  visual 151, craft 125, systems 112, tooling 106, … swiftui 1, react-native 1. VERIFIED (grep count).

### 2.2 How routing / progressive disclosure / specificity actually work
- **There is no routing/selection algorithm in code.** "Route by topic, then stack, then specificity…
  Prefer 1 skill… Never use more than 3… Prefer framework-specific skills when the stack is obvious" is
  **natural-language instruction to the agent** in skills/ui-skills-root/SKILL.md:22-59. VERIFIED.
- Progressive disclosure is a 3-step CLI protocol the agent follows: `start` (prints the routing skill)
  → `categories` (26 slugs) → `list --category X` (one line per skill: `pathSlug — topics — description`)
  → `get <slug>` (full markdown). VERIFIED bin/ui-skills.ts:92-110, 153-159, 235-306.
- Category filter = exact membership `skill.topics.includes(category)` after lowercase/trim; unknown
  category → exit 3 (bin/ui-skills.ts:176-197). No ranking, no fuzzy match.
- `get` resolution: exact `pathSlug` match first, else all exact `slug` matches; >1 → "Ambiguous" with
  candidates, exit 3 (bin/ui-skills.ts:161-174, 199-220). `pathSlug` = `user/slug`, or
  `user/repo/slug` on collision (registry.ts:5252-5278).
- `relatedTopicSlugs` / `primaryDesignTopicSlugs` (src/data/topics.ts:190-228) are used for site
  navigation (INFERRED from names/AGENTS.md:76; not used by CLI or MCP — VERIFIED by reading both).
- Ordering: `ui-skills-root`, then `baseline-ui`, then alphabetical by pathSlug (src/data/skills.ts:339-348).

### 2.3 CLI UX (VERIFIED bin/ui-skills.ts)
- `npx ui-skills [start | categories | list [--category <topic>] | get <slug>]`; `-h/--help/help`.
- **Network-only**: fetches `${UI_SKILLS_SITE_URL ?? https://www.ui-skills.com}/skills/registry.json`
  and `/skills/<pathSlug>/llms.txt` (lines 80-81, 127-151). No local cache, no offline mode.
- Exit codes: 1 usage/extra args/missing value; 2 unknown command; 3 not found/ambiguous/unknown
  category; 4 network error (lines 118-125, 187-193, 204-215, 308-315).
- Runs as `node --import tsx bin/ui-skills.ts` (bin/ui-skills.js:25-39) — tsx is a runtime dependency.
- Skill content served by the site is a **build-time snapshot** of upstream raw GitHub files
  (`prerender = true`, src/pages/skills/[...slug]/llms.txt.ts; remote fetch with 24h cache, 1 MiB cap,
  5 s timeout, src/lib/remote-skill.ts:1-4, 101-161). No content pinning to commit SHAs; sha256
  digests are recorded in src/data/agent-skills-digests.json and exposed in discovery
  (src/lib/agent-skills-discovery.ts:109-116, 154-213).

### 2.4 MCP / discovery (VERIFIED)
- Remote **streamable-HTTP JSON-RPC** at `https://www.ui-skills.com/mcp` (also `/mcp/rpc`), stateless,
  no auth, CORS `*`, 64 KiB request cap; protocolVersion `2025-06-18`
  (src/lib/mcp-server.ts:19-26, 109-135; src/pages/mcp.ts; src/pages/mcp/rpc.ts).
- Tools: **`list_skills`** `{query?: string}` (case-insensitive substring over slug/pathSlug/name/
  description; returns JSON text `{source,count,skills[{name,pathSlug,description,url}]}`) and
  **`get_skill`** `{name: string}` (discovery name, slug, pathSlug, or `endsWith('/'+name)`)
  (mcp-server.ts:51-84, 148-214; agent-skills-discovery.ts:92-103). No topic filter in MCP.
- Browser WebMCP tools `list_ui_skills`, `open_ui_skill` via `navigator.modelContext`
  (src/lib/webmcp-tools.ts:29-30, 77, 109).
- Agent Skills discovery index `/.well-known/agent-skills/index.json` with
  `$schema: https://schemas.agentskills.io/discovery/0.2.0/schema.json`, entries
  `{name,type:"skill-md",description(≤1024),url,digest}` (agent-skills-discovery.ts:8, 215-230);
  MCP server card at `/.well-known/mcp/server-card.json` (agent-skills-discovery.ts:232-261).
  Discovery names: lowercase, `/`→`-`, `[a-z0-9-]`, ≤64 chars (agent-skills-discovery.ts:74-82).

### 2.5 Skill file format (VERIFIED)
- SKILL.md with YAML frontmatter. Fields observed in the 7 local skills: `name` (all), `description`
  (all; may be folded `>` scalar), `license` (2: ui-skills-root, fixing-metadata), `version`
  (fixing-metadata, top-level), `metadata: {author, version}` (ui-skills-root:4-7). Matches the
  Agent Skills convention (name/description required; others optional) — INFERRED.
- Optional `agents/openai.yaml` per skill (Codex/ChatGPT interface metadata: display_name,
  short_description, default_prompt, policy.products, allow_implicit_invocation)
  (skills/improve-ui/agents/openai.yaml). Optional `references/*.md`.
- Registry entry type (src/data/registry.ts:1-15): `slug, pathSlug, sourceKey, sourceLabel, user,
  repo, rawUrl, githubUrl, name, description, topics?, files?` — **no license field per skill**; repo
  licenses only in src/data/github-metadata.json (from GitHub API, `generatedAt` 2026-10-05).
- Skill content style: RFC-2119-ish MUST/SHOULD/NEVER checklists (e.g. skills/baseline-ui/SKILL.md),
  opinionated stack choices (Tailwind defaults, `motion/react`, `cn`), i.e. subjective conventions
  mixed with some objective a11y rules (fixing-accessibility).

### 2.6 Tests (VERIFIED)
14 test files under tests/ (node:test via tsx), ~63 `test(` calls, incl. cli.test.ts (4),
ui-skills-cli.test.ts (2), skills-mcp-discovery.test.ts (5), webmcp-tools.test.ts (2); plus smoke.mjs.
`npm run check` = typecheck + check:skills + test + build + smoke; release workflow publishes to npm on
`v*.*.*` tag (.github/workflows/release.yml).

### 2.7 Assessment
- Recommended integration: **treat as an external, optional skill catalog**. RepoMarshal can (a) emit
  a recommendation like "UI change detected → consider `npx ui-skills start` / MCP `get_skill
  baseline-ui`", or (b) optionally query `registry.json` / MCP at plan time behind a network opt-in.
  Do not bundle third-party skills (each has its own upstream license; registry doesn't track it).
  Reimplementing the *pattern* (category list → short list → load one) is idea-level, not code.
- Attribution: MIT (retain copyright + permission notice) only if code or the 7 first-party SKILL.md
  files are copied; third-party skills: per-upstream license, UNKNOWN per entry.
- Compatibility concerns: network-only CLI conflicts with local-first; content is unpinned upstream
  `main` snapshots (supply-chain/prompt-injection surface — RepoMarshal should treat fetched skill text
  as untrusted data); version skew (pkg 0.2.4 vs tag 0.2.3); MCP is remote-only, unauthenticated.
- Strengths: simple, legible progressive-disclosure protocol; cap of ≤3 skills; topic taxonomy +
  related-topic graph; Agent Skills discovery + digests; tiny CLI with clear exit codes.
- Weaknesses: selection is entirely LLM-judgement (no deterministic routing, no repo/stack detection,
  no scoring); no offline mode; topic tags are hand-curated and skewed (frontend on 259/395).

---

## 3. UI/UX Pro Max (`nextlevelbuilder/ui-ux-pro-max-skill`)

| Field | Value |
|---|---|
| Identifier | github.com/nextlevelbuilder/ui-ux-pro-max-skill; npm `ui-ux-pro-max-cli` (bin `uipro`); Claude plugin `ui-ux-pro-max` |
| SHA | `7246805992a42b7bba37d4cac83b01416f0c6c44` |
| Last commit | 2026-10-12T18:08:03+07:00 "feat: add OpenClaw platform support (#527)" |
| History | 275 commits, first `ce4372c` 2025-11-30 (rebranded from "Antigravity Kit", a9a9715) |
| Tags | v2.15.0 (2026-08-14), v2.14.2 (08-12), v2.14.1/v2.14.0 (08-06), v2.13.0 (08-03)… Commits after v2.15.0 untagged |
| Versions | skill.json / plugin.json 2.15.0; cli/package.json **2.5.0** (separate numbering) |
| Stack | Python 3 stdlib only (search engine), TS/Node installer CLI (commander, prompts, ora, chalk), Next.js `gallery/`, sample `projects/`, `stack/` sample Claude Code project |

### 3.1 Licenses per component (VERIFIED)
- Root LICENSE: MIT, "Copyright (c) 2024 Next Level Builder" (LICENSE:1-3). License history:
  `70200ed` (2025-12-02, file added with CLI) → `363cd89` (2025-12-05, "chore: change license to MIT
  (#19)"). INFERRED: pre-2025-12-05 license was something else (not inspected in detail); current is MIT.
- skill.json, .claude-plugin/plugin.json, cli/package.json, stack/package.json: `MIT`.
- `.claude/skills/ui-styling/` (+ mirror `cli/assets/skills/ui-styling/`): **LICENSE.txt = Apache-2.0**
  (201 lines, unfilled `Copyright [yyyy]` boilerplate), while its SKILL.md frontmatter says
  `license: MIT` (ui-styling/SKILL.md:5) — **conflict**. Added `1c2cb32` 2026-03-10. Contains
  `canvas-fonts/` (81 files, 5.5 MB TTFs each with `*-OFL.txt` → SIL OFL 1.1). INFERRED: derived from
  another (Apache-2.0) skills collection; provenance not documented.
- Bundled skills `banner-design, brand, design, design-system, slides, ui-styling` have
  `metadata.author: claudekit` (their SKILL.md frontmatter) — third-party origin, licensing per
  frontmatter MIT where stated (brand, slides: no license field). UNKNOWN upstream terms.
- Data provenance: `data/google-font-licenses.json` per-family license (OFL/APACHE2/…), from
  google/fonts `METADATA.pb` @ revision `038b637d…` (1,934 families); `phosphor-icons-upstream.json`
  from `@phosphor-icons/core` 2.1.1 (MIT per test fixture). Facts/metadata only, but compiled
  databases (CSV rows, prose guidance) are copyrightable expression → MIT applies.

### 3.2 What the data is (VERIFIED src/ui-ux-pro-max/data/, the source of truth per CLAUDE.md:94-101)
- **39 files, 3,093,030 bytes** (data/), 34 CSV + 4 JSON (+ stacks/ = 22 CSVs). **4,319 CSV data rows**
  total (python csv count). scripts/ 438,545 B; templates/ 86,774 B. Mirrored copies in
  `cli/assets/` and `.claude/skills/ui-ux-pro-max/` (CI-enforced sync).
- Per-file rows: styles 88 (50 active / 29 supplemental / 9 deprecated → "79 searchable"), products 192,
  colors 192 (shadcn-like token set: Primary/On Primary/…/Ring), ui-reasoning 192, typography 74,
  google-fonts 1,934, icons 105, ux-guidelines 119 (Critical 4/High 44/Medium 62/Low 9), charts 25,
  landing 34, motion 17 (GSAP), app-interface 32, react-performance 44, 22 stack CSVs totalling 1,271
  rows (13 cols incl. `Docs URL, Applies To, Status, Verified At`). Counts match
  data/catalog-summary.json `counts` (verifiedAt 2026-08-26).
- `data-provenance.json` (36,686 B): per-entity `status`, `verifiedAt`, `sla` (manual-verified 365 d /
  needs-review 90 d), `confidence` (e.g. 0.88), `sources[{type: official|derived, ref}]`.

### 3.3 Search / query (VERIFIED src/ui-ux-pro-max/scripts/)
- Entry: `python3 scripts/search.py "<query>" [--domain/-d D] [--stack/-s S] [-n 1..20, default 3]
  [--json] [--full] [--design-system/-ds] [-p NAME] [-f ascii|markdown] [--persist] [--page P]
  [-o DIR] [--force] [--variance 1-10] [--motion 1-10] [--density 1-10]` (search.py:98-117).
  Domains (12): style, color, chart, landing, product, ux, typography, google-fonts, icons, gsap,
  react, web. Stacks (22): react, nextjs, vue, svelte, astro, swiftui, react-native, flutter, nuxtjs,
  nuxt-ui, html-tailwind, shadcn, jetpack-compose, threejs, angular, laravel, javafx, wpf, winui,
  avalonia, uno, uwp (core.py STACK_CONFIG ~lines 89-111).
- Engine: **pure-Python BM25** (k1=1.5, b=0.75; core.py:282-345) over concatenated per-domain
  `search_cols`, with synonym normalisation (`_normalize`, core.py:273-279), stopwords, tokens ≥2
  chars. "Regex hybrid" in CLAUDE.md = phrase/identity regex matching for exact style/pattern IDs and
  hex-colour detection, not a separate regex index (core.py:589-593, 628-661, 664-704).
- **Abstention/calibration**: per-domain BM25 score floors (style 4.3, landing 4.0, product 6.0, icons
  5.8, react 3.3), landing min token coverage 0.5, stack floor 3.6 + coverage ⅓; below threshold →
  0 results + `suggestions` (core.py:196-213, 414-463). Calibration version "2026-08-12-v1"; relevance
  evaluation suite with calibration/held-out splits (scripts/evaluate-relevance.py,
  tests/fixtures/relevance-*.json). Verified by running: nonsense query → `count: 0, suggestions: []`.
- Domain auto-detect when `--domain` omitted: keyword-phrase scoring weighted by phrase length,
  fixed tiebreak order, default `style` (core.py:628-661).
- Caching: in-process CSV + BM25 index cache keyed by mtime/size (core.py:350-403). No persistent index.
- Output: Markdown-ish text or `--json` `{domain, query, file, count, results[], suggestions?}`.

### 3.4 "Stack-aware" recommendations (VERIFIED)
- Stack is **explicit** (`--stack`); the Python engine does **no repo/stack detection**
  (no `package.json` reading in scripts — grep). `--stack` is ignored in `--design-system` mode
  (search.py:122-128).
- Stack search filters rows by lifecycle generation: `active` rows only by default, `deprecated` rows
  only if query asks for legacy, shadcn base filtering (Radix / Base UI / React Aria) via `Applies To:
  base=` (core.py:888-923); current major versions table `WEB_STACK_CURRENT_MAJORS` /
  `STACK_CURRENT_VERSIONS` (core.py ~123-150).
- `--design-system`: multi-domain search driven by `ui-reasoning.csv` rule matched on product category
  (`Decision_Rules` JSON like `{"if_ux_focused":["style:…"]}`), style priority, colour/typography mood;
  persisted as `design-system/<slug>/MASTER.md` + `pages/<page>.md` (design_system.py:283-420, 944-1100).
- Installer `uipro` detects the **AI platform** (dirs `.claude`, `.cursor`, `.windsurf`, `.agents`,
  `.github`, `.kiro`, `.codex`, `.roo`… cli/src/utils/detect.ts:13-40), not the UI stack.

### 3.5 Installer CLI `uipro` (VERIFIED cli/src/index.ts)
`init [-a/--ai <type>] [-f/--force] [-o/--offline] [-g/--global] [-t/--token] [--dry-run]`,
`versions [-t]`, `update [-a] [-g] [-t]`, `uninstall [-a] [-g]`. 24 AI types incl. `universal`, `all`
(cli/src/types/index.ts:46). Generates per-platform SKILL/workflow files from
`templates/base/*.md` + `templates/platforms/<platform>.json` (frontmatter, scriptPath, folderStructure).
No MCP server of its own; `stack/.mcp.json` only configures third-party MCPs (playwright,
chrome-devtools, shadcn) for a sample project.

### 3.6 Objective vs subjective content
- **Objective / checkable** (VERIFIED): WCAG relative-luminance + contrast ratio implementation
  (validate_data.py:148-160; design_system.py:129-160); data contract requires text pairs ≥ 4.5:1 and
  focus `Ring` vs Background ≥ 3.0:1 (validate_data.py:76-83); design-system picks primary/outline
  colours meeting ≥3 / ≥4.5 (design_system.py:203, 277); dark-surface threshold luminance < 0.18
  (design_system.py:126); SKILL.md priority table: contrast 4.5:1, touch target 44×44 px, 8 px spacing
  (.claude/skills/ui-ux-pro-max/SKILL.md:22-23); responsive check breakpoints 375/768/1024/1440
  (design_system.py:804-807); font-licence and icon-import validity, Google Fonts URL/weight checks,
  stack rows with official `Docs URL`/`Verified At`; validator rejects unsupported "WCAG AA compliant"
  claims and quantified conversion claims (validate_data.py:42-46, 758-766).
- **Subjective heuristics** (VERIFIED as data, judged INFERRED): style catalogue (glassmorphism,
  brutalism…), product→style/palette/landing mappings, colour "mood", font-pairing "mood", ui-reasoning
  rules with self-assigned `Confidence`, style `Accessibility: risk:low|conditional|high`, landing
  "conversion optimisation" prose, motion "intensity" dials (variance/motion/density 1-10), stack
  "Do/Don't" severity labels.

### 3.7 Tests (VERIFIED)
13 Python unittest files, 164 `def test_` (scripts/tests/), incl. data contracts, relevance
evaluator, stack freshness, design-system mode; 6 Playwright e2e specs for the installer
(cli/tests/e2e/); CI workflows: tests, smoke-stacks, check-asset-sync, refresh-catalogs (review-only
upstream catalog candidates), release, bump-versions. `prepublishOnly` runs full `verify:data`.

### 3.8 Assessment
- Recommended integration: **optional external tool**: if a change touches UI files and the user has
  UUPM installed, RepoMarshal's plan can recommend/run `python3 <skill>/scripts/search.py "<q>"
  --stack <detected> --json` (RepoMarshal does the stack detection it lacks) and attach results as
  advisory context. Treat output as *advice*, never as PASS/FAIL evidence, except the objective
  checks (contrast ≥4.5/3.0, touch ≥44px) which RepoMarshal should implement itself from WCAG, not copy.
  MIT would permit vendoring CSVs, but vendoring 3 MB of curated, fast-moving, partly-subjective data
  is not recommended; if any data/code is copied, include MIT notice and check per-file provenance
  (exclude `ui-styling/` Apache/OFL assets).
- Attribution obligations: MIT notice for any copied portion; Apache-2.0 (+NOTICE handling) and OFL for
  `ui-styling` assets/fonts if copied; third-party "claudekit" skills unclear.
- Compatibility concerns: licence inconsistency in ui-styling; dual version numbers; three mirrored
  copies of data; Python 3 required; `--persist` writes into the target repo (`design-system/`);
  `-o` defaults to cwd.
- Strengths: local-first, stdlib-only, deterministic BM25 with calibrated abstention and diagnostics;
  JSON output; data contracts + provenance/freshness SLAs + relevance eval — strong prior art for
  RepoMarshal's "UNKNOWN instead of guessing" posture and data validation.
- Weaknesses: much subjective taste data; no stack auto-detect; ranking is lexical (no semantics);
  marketing-heavy README; big multi-platform installer surface.

---

## 4. Cross-cutting takeaways for RepoMarshal (INFERRED)
1. None of the three does deterministic, change-aware skill selection; UI Skills explicitly delegates
   it to the LLM, UUPM requires the agent to name domain/stack. Gap RepoMarshal can fill: map changed
   files + detected stack → recommended skills/checks deterministically, with UNKNOWN when unsure.
2. Good patterns to re-implement (ideas only): progressive disclosure (catalog → shortlist → load ≤3);
   BM25 with score/coverage floors that **abstain**; provenance records with `verifiedAt` + SLA;
   spec frontmatter `status` + `open_questions` + "Success Criteria" section as evidence anchors.
3. Licence posture: Chisel = interop-only (read files / optional subprocess; no code, no bundling).
   UI Skills and UUPM = MIT, safe to reference; avoid bundling third-party skill content.
