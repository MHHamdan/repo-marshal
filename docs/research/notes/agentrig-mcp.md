# Prior art: AgentRig (x2) and the MCP specification

Research date: 2026-10-13. Read-only inspection of local clones; no code copied.
Labels: **VERIFIED** (path:line in the clone at the SHA given), **INFERRED** (general knowledge or reasoning, not checked against source), **UNKNOWN**.
Clone root: `<research-clones>/` (the directory names use single underscores, e.g. `doidor_agentrig`).

---

## 1. doidor/agentrig ("meta harness")

| Field | Value |
|---|---|
| Identifier | github.com/doidor/agentrig |
| SHA (HEAD) | `05d0dd5e8c8276faa57d786080dc3b18c6c74d7b` |
| Last commit | 2026-10-02 14:11:59 +0200, "feat: require general rules and publish full skill catalog (#27)" |
| First commit | 2026-06-06; 51 commits; 41 by Tudor Popa, 10 by github-actions bot |
| Tags | v0.6.0 … v0.11.3, v0.12.0 (v0.12.0 = `fededa1`, 2026-07-29). Spec 2.0 has no tag (AGENT-RIG.md:3). |
| License | MIT. The copyright line reads "Copyright (c) 2026 Microsoft Corporation" (LICENSE:1-3). The 0.12.0 package.json `"license": "MIT"` also matches. VERIFIED |
| Activity | 4 commits since 2026-07-01 and 2 since 2026-09-01. It is a single-maintainer project and changed its whole architecture 12 days ago. |

`git log --oneline -15` (VERIFIED):
```
05d0dd5 feat: require general rules and publish full skill catalog (#27)
7548d2f Refactor AgentRig into a document-first harness (#26)   <- 2026-10-01
fededa1 chore: version packages (#25)                            <- v0.12.0
fae869b docs: recreate the harness without the CLI via the docsite llms.txt (#23)
ce544d8 feat(knowledge): adopt Epichan harness patterns (security-reviewer + ops skills) (#24)
a78b075 chore: version packages (#22)
14c7c4b Remove internal source-repo references from harness content (#21)
d82ccba chore: version packages (#20)
dd90c32 docs: surface `eval --scaffold` (repo-tailored eval generation) earlier (#19)
ca33b03 chore: version packages (#18)
900c39c Extract agent prompts to markdown + harness content quality fixes (#17)
a7ba0d8 chore: version packages (#16)
3d04ba8 feat(update,fix,doctor): auto-populate markers, validate YAML/model ids, add agentrig fix + --auto-fix (#15)
d13237e chore: version packages (#13)
e75cb49 fix(ci): docs Pages OIDC perms + add changeset for eval-rebuild release (#12)
```

### Functionality at HEAD (document-first, no CLI)
- **VERIFIED.** HEAD is a *specification*, not a tool. It contains "no AgentRig installer, npm CLI, model runtime, or automatic migration" (README.md:5-7). The full contract is in AGENT-RIG.md (spec version 2.0, AGENT-RIG.md:3).
- **VERIFIED.** Bootstrap works by pasting a prompt that points an agent at `https://tudorpopa.com/agentrig/llms.txt`. The agent then tailors the harness itself (README.md:16-26; AGENT-RIG.md:83-111).
- **VERIFIED.** The Core profile requires these files (AGENT-RIG.md:41-52):
  - `AGENTS.md` with `## Purpose` and `## Commands`, where Build, Test and Lint are listed explicitly;
  - `.agents/agents/builder.md`;
  - `.agents/agents/paranoid.md`, a frozen independent-reviewer prompt;
  - `.agents/skills/self-verify/SKILL.md`;
  - four rules in `.agents/rules/`: engineering-principles, security, code-review and no-debug-logging.
- **VERIFIED.** Four procedural skills are optional: fix-ci, address-review-comments, resolve-conflicts and log-gotcha (AGENT-RIG.md:58-63).
- **VERIFIED.** Frontmatter contract (AGENT-RIG.md:65-66):
  - skills need `description` and `allowed-tools`;
  - rules need `globs`, `description` and `priority` (example: `.agents/rules/security.md:1-5`, with `priority: 1` and `globs: ["**/*"]`).
- **VERIFIED.** `validate.mjs` (410 lines, Node built-ins only) is a static structure checker. The spec says it does not prove the agent's behaviour (AGENT-RIG.md:106-111, 129-152).
- **VERIFIED.** Multi-host projection is now explicitly **anti-pattern by default**. The spec says to "Author just the native adapter(s) those hosts need … avoid a compiler or committed generated copies by default" (AGENT-RIG.md:119). The repo's own instructions say "Do not autogenerate or commit vendor-specific instruction copies" (AGENTS.md:23-24).
- **VERIFIED.** The CLI equivalents were removed. `agentrig compile`, `eval`, `--scaffold` and `dashboard` are gone, along with generated vendor instructions and MCP/setup files (MIGRATION.md:20-37). The CLI survives only as the historical package `@doidor/agentrig@0.12.0` (MIGRATION.md:39-42).
- **VERIFIED.** There is no provenance metadata, no registry and no MCP server.

### Historical CLI (v0.12.0, `fededa1`). Useful as a design reference only
- **VERIFIED.** The package was `@doidor/agentrig` 0.12.0, bin `agentrig`. Its dependencies were `@github/copilot-sdk`, `yaml` and `zod`, with `@anthropic-ai/claude-agent-sdk` as an optional peer (`git show v0.12.0:package.json`).
- **VERIFIED.** Commands: `init`, `update [--auto-fix]`, `compile`, `fix`, `eval [--static|--scaffold|--min N|--json]`, `doctor [--json]`, `dashboard` (`git show v0.12.0:docs/commands.md`).
- **VERIFIED.** The canonical source was `AGENTS.md`, `.agents/rules/*.md`, `.agents/skills/*/SKILL.md` and `.mcp.json` (`v0.12.0:docs/agent-surfaces.md:13-20`). The projection map was (`v0.12.0:docs/agent-surfaces.md:22-44`):

  | Host | Projected output |
  |---|---|
  | Copilot | `.github/copilot-instructions.md`; `.github/instructions/<rule>.instructions.md` with `applyTo`; `.github/copilot/mcp.json`; `copilot-setup-steps.yml` |
  | Claude | `CLAUDE.md` using an `@AGENTS.md` import plus an inlined fallback; `.claude/` symlinked to `.agents/` |
  | Cursor | `.cursor/rules/*.mdc`, with `alwaysApply` set when globs is `**/*` |
  | Codex and OpenCode | `.codex/` and `.opencode/` symlinked to `.agents/` |
  | VS Code | `.vscode/mcp.json` |

- **VERIFIED.** Evaluation had three layers (`v0.12.0:docs/evals.md`):
  - **A1:** an install-completeness check;
  - **A2:** quality probes;
  - **B:** fixture scenarios scored by a deterministic oracle and an independent judge, with paired sign-test lift against a baseline and judge calibration.
- **VERIFIED.** Results were written to `.agentrig/eval/results/` with `schemaVersion: 2`.

### Overlap with RepoMarshal
- **Low on the core problem.** doidor/agentrig installs a *static*, repo-wide harness. It does not compute a per-change plan from a task plus a diff.
- **Medium on gates and evidence.** RepoMarshal's gates and evidence overlap with its Builder self-verify (baseline and after evidence), its independent-review discipline and the Build/Test/Lint commands contract.

### Integration opportunity
- **INFERRED.** RepoMarshal could read an AgentRig-conformant repo as policy input:
  - the `AGENTS.md` `## Commands` section gives the Build/Test/Lint gate commands;
  - the rule `globs` and `priority` fields allow rules to be selected by changed path;
  - the `.agents/skills` directory is a skill inventory to pick from.
- The data is cheap to parse, and the validator shows the minimum shape it guarantees.
- Do not depend on it as a projection engine: projection was removed upstream.

**Strengths:** a clear minimal contract; rule frontmatter with globs and priority that maps directly to change-aware selection; strong evidence and review norms; a thoughtful historical eval design.

**Weaknesses:**
- The architecture is volatile (a hard pivot on 2026-10-01).
- There is one maintainer and no CLI or API to integrate with.
- `allowed-tools` is required, but it is a Claude-flavoured field that is optional in Agent Skills.
- The rules format (`globs`, `priority`) is AgentRig-specific, not a standard.

---

## 2. regenrek/agentrig ("portable agent plugin ecosystem")

| Field | Value |
|---|---|
| Identifier | github.com/regenrek/agentrig. npm `agentrig` (CLI) and `@agentrig/sdk`. Docs at docs.agentrig.ai |
| SHA (HEAD) | `50b6983d2f6264511062be4f1b8877615f79639b` |
| Last commit | 2026-08-13 16:13:31 +0200 (merge of #70, docs for v2.0.2 contract sync) |
| First commit | 2026-01-24; 165 commits; 160 by Kevin Kern |
| Tags | … v0.8.2, v2.0.0 (2026-08-12), v2.0.1, v2.0.2 (2026-08-13) |
| License | MIT, with "Copyright (c) 2026" and no named holder (LICENSE:1-3). `packages/cli/package.json` and `packages/sdk/package.json` both declare `"license": "MIT"`. VERIFIED |
| Activity | 18 commits since 2026-07-01 and 0 since 2026-09-01. Active through August, quiet for about two months. Effectively one maintainer. |

`git log --oneline -15` (VERIFIED):
```
50b6983 Merge pull request #70 from regenrek/docs/v2.0.2-contract-sync
cb54e33 docs: align AgentRig 2.0.2 contracts
1e40458 Merge pull request #69 from regenrek/feat/agentrig-v2-standard-first
08ffc01 Merge remote-tracking branch 'origin/main' into feat/agentrig-v2-standard-first
601c679 fix: close AgentRig 2.0.2 dogfood gaps
8d70e47 Merge pull request #68 from regenrek/release/v2.0.1
b1fcd7e chore: release v2.0.1
efa6be9 Merge pull request #67 from regenrek/fix/v2-runtime-neutral-sdk-root
9b3a13f fix: isolate filesystem APIs from SDK root
1e23bb9 Merge pull request #66 from regenrek/feat/agentrig-v2-standard-first
a27bc23 chore: prepare AgentRig 2.0.0 release
09da986 fix: enforce Agent Plugins package boundaries
25dd1e8 feat!: hard-cut AgentRig 2.0 to Agent Plugins v1
773ed68 Merge pull request #65 from regenrek/feat/agent-plugins-v1-adoption
bf999a9 chore(ci): refresh Vite+ playground fixture
```

### Package metadata and layout (VERIFIED)
- It is a pnpm workspace using Vite+ (`package.json`). It contains:
  - `packages/cli`: `agentrig@2.0.2`, bin `agentrig`, dependencies `citty`, `giget`, `zod` and `@zip.js/zip.js`, Node 18 or later;
  - `packages/sdk`: `@agentrig/sdk@2.0.1`, dependencies `yaml`, `zod` and `@zip.js/zip.js`;
  - `apps/docs` (TanStack and fumadocs-style MDX);
  - `templates/plugin-starter`.
- The SDK contains the repo scanner (`repo-scan/`), provider materializers (`provider/`), capability resolution (`capabilities/`), file-system adapters (local, zip, tar, GitHub tree) and an enrichment contract.

### CLI (VERIFIED: packages/cli/src/cli.ts:22-39; README.md:21-122)
- Top-level commands: `init`, `install`, `search`, `view`, `list`, `inspect`, `use`, `doctor`, `skill`, `mcp`, `hook`, `login`, `logout`, `whoami`, `rig`, `registry`, `plugin`.
- Examples from the README:
  - `agentrig plugin install <codex|claude|cursor> agentrig/agentrig.core-committer`
  - `agentrig install codex <ref> --pick skill:<name> --pick mcp:<name>`
  - `agentrig skill|mcp|hook install <provider> <ref> --pick <name>`
  - `agentrig inspect <owner/repo>`
  - `agentrig use <repo> --as-plugin <ns.name> --pick skills/foo [--install --provider codex] [--enrich-ai=local]`
  - `agentrig registry add <alias> <url>`
  - `agentrig plugin init|create|export|bundle|submit|status|uninstall`
  - `agentrig rig apply <provider> <rig> --scope workspace`
- **Optional AI enrichment** works only with your own key, against an OpenAI-compatible endpoint (README.md:84-96).

### Canonical source format (VERIFIED)
AgentRig 2.0 hard-cut to **Agent Plugins 1.0.0**, an external spec at agent-plugins.org (docs/adr/0001-adopt-agent-plugins-v1.md:1-14). The ADR itself says that spec is a **Working Draft** (ADR:12, 112).

```
plugin-root/
  plugin.json        # $schema https://agent-plugins.org/schemas/1.0.0/plugin.schema.json
  skills/<skill>/SKILL.md
  mcp.json           # $schema …/1.0.0/mcp.schema.json, { mcpServers: {...} }
  ai.agentrig/       # vendor extension dir (hooks, commands, agents live here)
```
(ADR:20-42; packages/sdk/src/agent-plugins.ts:4-7)

- **Portable components** are only skills and MCP servers. Commands, agents, hooks and rules are not portable and sit under the `ai.agentrig` extension (ADR:12, 40).
- **The manifest** contains `$schema`, `name`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords` and `extensions` (agent-plugins.ts:92-107).
- **The `extensions["ai.agentrig"]` block** holds `profile`, `capabilities` (required, optional and provided, with a fallback for each), `pluginDependencies`, `publicSkills`/`supportSkills`, `aliases`, `security` (`requiresConsent`, `showsExactCommands`, `requiresEnvVars`) and `permissions` (agent-plugins.ts:74-89).
- **Capability IDs are a closed list:** `plan.ledger`, `docs.latest`, `browser.verify`, `ci.status`, `secrets.scan`, `mcp.verify` and others (agent-plugins.ts:11-26).
- **The MCP entry schema** (agent-plugins.ts:172-215) is a discriminated union over three server types:
  - `stdio`: `command` must be a bare executable or a `./` path; `args`, `env`;
  - `streamable-http`: the URL must be HTTPS, or HTTP on loopback only, with no credentials;
  - `sse`.
- **The skill frontmatter** follows Agent Skills (agentskills.io): `name` (1-64 characters, lowercase and hyphens, must equal the directory name), `description` (1-1024 characters), and optional `license`, `compatibility`, `metadata` (a string map) and `allowed-tools` (packages/sdk/src/agent-skills.ts:8-16, 70-71).
- **Provider projection.** The output is a provider-native **plugin and marketplace**, not repo instruction files:
  - Claude: `.claude-plugin/plugin.json`, a marketplace pointer, then `claude plugin marketplace add` and `claude plugin install` (packages/cli/src/lib/plugin-providers/claude.ts:174-178);
  - Codex: `.codex-plugin/plugin.json` plus `.agents/plugins/marketplace.json` (codex.ts:106, 179);
  - Cursor: `.cursor-plugin/plugin.json` in `.cursor/plugins/local` (cursor.ts:38-40).
- **Per-provider MCP compilation** happens in `packages/sdk/src/provider/mcp-compiler.ts:4-42`.
- **Hooks** are emitted for Claude and Cursor only (claude.ts:88, cursor.ts:49, 68). The hook fixture uses the Claude `PreToolUse` matcher shape (packages/sdk/tests/fixtures/dots-like/hooks.json).
- **For Codex,** it writes an `AGENTS.md` *pointer* inside the plugin directory (provider-pointers.ts:17).
- **No Copilot or Gemini CLI target** exists: `PROVIDER_TARGETS = ['codex','claude-code','cursor']` (agent-plugins.ts:10).
- **It does not generate project `AGENTS.md`, `CLAUDE.md` or `.cursor/rules`** from a canonical source. `doctor` only checks plugin-level pointers (packages/cli/src/commands/doctor.ts:955).

### Registry, trust and provenance (VERIFIED)
- **A registry is a static folder** (apps/docs/content/docs/registry/hosting.mdx:9-28):
  - `registry.json`, which is signed;
  - `plugins|skills|mcps|hooks/<ns>/<name>/versions/<ver>/**`;
  - `advisories.json`;
  - for each version, `AGENTRIG_SOURCE.json`, `AGENTRIG_LOCK.json` and `AGENTRIG_REVIEW.json`.
- **Trust tiers** are `official`, `reviewed`, `listed`, `blocked` and `yanked`. Only `official` and `reviewed` can be installed. `verified-author` is identity only and grants no trust (registry/trust.mdx:11-33).
- **Provenance, digests, risk and approvals** are kept *outside* the package as control-plane state (ADR:53-56).
- **Install ledger:** installs are hash-owned (sha256), and uninstall removes only values that still match (README.md:62-71; packages/cli/src/lib/plugin-install-ledger.ts:55-66).
- **Rigs** are named plugin sets in `agentrig.config.json`, with `extends` and `defaultRig` (rigs/defining.mdx:9-31).
- **Harness evaluation:** none. There is `doctor`, which checks configuration and export health. No behavioural eval was found in `packages/*/src`.

### Overlap with RepoMarshal
- **Low on planning.** regenrek/agentrig is a package manager and distributor (install, trust, materialize). It does not decide which skills or gates a given change needs.
- **Medium on skill and MCP inventory and on materialization** for Claude, Codex and Cursor.

### Integration opportunity
- **INFERRED.** RepoMarshal could ship *itself* as an Agent Plugins v1 package:
  - a root `plugin.json`;
  - `skills/repomarshal-plan/SKILL.md`, which tells the agent to call the planner before editing;
  - `mcp.json` with one `stdio` entry such as `{"type":"stdio","command":"repomarshal","args":["mcp"]}`.
- AgentRig could then project that package into Claude, Codex and Cursor plugins.
- RepoMarshal could also *read* installed AgentRig ledgers or registries as a skill inventory when it chooses which skills to recommend.
- **Caveats:** Agent Plugins is a Working Draft, and the provider coverage leaves out Copilot and Gemini.

**Strengths:** standards-first (Agent Plugins and Agent Skills); a strong trust and provenance model with signed registries and hash ledgers; closure-checked selective installs; a runtime-neutral SDK; strict MCP URL and command validation.

**Weaknesses:**
- It depends on a Working Draft spec and already made one breaking hard-cut (v0.8 to v2.0).
- Only three providers are supported.
- There is one maintainer and no commits for about two months.
- It does not handle repo instruction files (`AGENTS.md`, rules).

---

## 3. modelcontextprotocol/modelcontextprotocol (MCP spec)

| Field | Value |
|---|---|
| Identifier | github.com/modelcontextprotocol/modelcontextprotocol |
| SHA (HEAD) | `3778656b2e0ba6725035e816fe3085374cbaa736` (2026-10-12) |
| Activity | 4866 commits, 472 since 2026-07-01. Very active, with multi-company maintainers. |
| Latest released spec | **2026-07-28**: tag `2026-07-28` = `5f5440bb` (2026-07-28); `LATEST_PROTOCOL_VERSION = "2026-07-28"` (schema/2026-07-28/schema.ts:30). The previous release was 2025-11-25. `draft/` also exists. VERIFIED |
| License | **Transitioning from MIT to Apache-2.0** (LICENSE:1-7). New code and spec contributions are Apache-2.0. Docs other than the specs are CC-BY-4.0. Some older contributions remain MIT. package.json says "SEE LICENSE IN LICENSE". VERIFIED |

### 2026-07-28 changes that matter (VERIFIED: docs/specification/2026-07-28/changelog.mdx)
- **Stateless protocol.**
  - The `initialize` handshake is gone. Every request carries the protocol version and client capabilities in `_meta`.
  - Servers MUST implement `server/discover`.
  - Protocol sessions and `Mcp-Session-Id` are removed. Cross-call state goes through explicit server-minted handles passed as tool arguments (changelog.mdx:12-16).
  - Streamable HTTP lost its GET endpoint and resumability. Notifications go through `subscriptions/listen` (changelog.mdx:18, 28).
- **MRTR replaces server-to-client requests.** A server asks for elicitation, sampling or roots by returning `resultType: "input_required"` with `inputRequests`. The client retries with `inputResponses`. Every result now carries `resultType` (changelog.mdx:24-26; basic/patterns/mrtr.mdx).
- **Tasks** moved out of core into an official extension, `io.modelcontextprotocol/tasks`, using `tasks/get`, `tasks/update` and `tasks/cancel` with polling (changelog.mdx:22; seps/2663-tasks-extension.md, Status Final).
- **Extensions framework.** An `extensions` field was added to the client and server capabilities (changelog.mdx:32; SEP-2133, Final).
- **Schema dialect.** `inputSchema` and `outputSchema` accept any JSON Schema 2020-12 keyword, and `structuredContent` can be any JSON value (changelog.mdx:49-52).
- **Caching and ordering.** `tools/list` and other list endpoints must carry `ttlMs` and `cacheScope`, and tools SHOULD be listed in deterministic order (changelog.mdx:34-36).
- **Deprecated, removable on or after 2027-07-28** (deprecated.mdx:26-31; SEP-2577):
  - **Roots, Sampling and Logging.** Roots should be replaced by passing directories as tool parameters or server config. *This directly affects RepoMarshal*: pass `repo_path` explicitly.
  - **HTTP+SSE.**
  - **Dynamic client registration**, in favour of Client ID Metadata Documents.
- **OpenTelemetry trace context** conventions were added for `_meta` (changelog.mdx:33).

### Primitives relevant to RepoMarshal
- **Tools** (VERIFIED: server/tools.mdx:280-306, 496-530; schema.ts:1912-1953):
  - fields are `name`, `title`, `description`, `icons`, `inputSchema` (2020-12 by default) and an optional `outputSchema`;
  - if `outputSchema` is present, the server MUST return conforming `structuredContent`, and SHOULD also include serialized JSON in a text block for backward compatibility;
  - results may carry `isError`, `resource_link` and embedded resources.
- **Tool annotations** are `readOnlyHint`, `destructiveHint` and `idempotentHint` (both meaningful only when not read-only) and `openWorldHint`. Clients MUST treat annotations as untrusted unless the server is trusted.
  - **INFERRED:** RepoMarshal's `plan` and `explain` tools should be `readOnlyHint: true`, `openWorldHint: false`.
- **Tool calls can return input-required results under MRTR** (tools.mdx:174).
  - **Stateful tools** pass explicit handles (tools.mdx:683-708). **INFERRED:** a `plan_id` returned by `plan` and passed back to `verify_gates` fits this pattern exactly.
- **Resources** (`resources/read`, templates, annotations) suit exposing plan artifacts and policy (`repomarshal://plan/<id>`). Prompts suit slash-command style entry points (server/prompts.mdx). VERIFIED that these files exist.
- **Elicitation** (client/elicitation.mdx:12-45):
  - it has a form mode (JSON schema) and a URL mode;
  - secrets MUST use URL mode;
  - it is delivered via MRTR in 2026-07-28.
  - **INFERRED:** useful for asking which ambiguous policy applies, but clients support it unevenly.
- **Transports** (basic/transports/index.mdx): stdio, meaning newline-delimited messages over a client-launched subprocess, and Streamable HTTP. **INFERRED:** stdio is the right default for a local-first tool.
- **Authorization:** OAuth applies to HTTP. "Implementations using an STDIO transport SHOULD NOT follow this specification" and take credentials from the environment instead (basic/authorization/index.mdx:21). VERIFIED

### Skills over MCP (VERIFIED: seps/2640-skills-extension.md, Status **Final**, extension ID `io.modelcontextprotocol/skills`)
- **Transport binding only.** The skill format is delegated entirely to the Agent Skills specification at agentskills.io (lines 19, 37-43).
- **Resources and methods.**
  - Every file in a skill is a resource under `skill://<skill-path>/<file>`.
  - `skills/list` and `skills/get` return entries with verbatim `frontmatter`, the `uri`, and `resources` listed as `{uri, digest, size}` triples or as `"dynamic"`.
- **Host obligations.** Hosts MUST verify digests and frontmatter, and MUST key a skill's identity by server plus URI (lines 48-94, 110-277).
- **Adoption is thin.** The client support matrix lists only mcpc as full support. ChatGPT, fast-agent and MCP Inspector are partial. **Claude, Cursor and VS Code Copilot show no Skills support** in the matrix (docs/extensions/client-matrix.mdx:31-50). VERIFIED (as documented; the matrix may lag reality).
- **Other relevant Final SEPs:**
  - Server Cards, `io.modelcontextprotocol/server-card`, for HTTP discovery (SEP-2127);
  - MCP Apps, `io.modelcontextprotocol/ui`, for interactive UI (SEP-1865).
  - No SEP covers "agent capabilities", "hooks" or "gates" as such. A grep of `seps/*.md` for "skill" matches only SEP-2640 and an incidental mention in SEP-2148.

**Strengths:** the de-facto standard, broadly supported by agents; a formal lifecycle and deprecation policy with a 12-month window; Skills over MCP is now a Final extension.

**Weaknesses and risks:** 2026-07-28 is a large breaking revision (stateless, MRTR), so SDK and client support will lag; Roots is deprecated; extension support across clients is sparse.

---

## 4. How agents consume instructions, skills, MCP and hooks. **All INFERRED** (general knowledge as of 2026, not verified here)

Confidence: H = high, M = medium, L = low.

| Agent | Instructions | Skills | MCP config | Hooks a gate could use |
|---|---|---|---|---|
| **Claude Code** | `CLAUDE.md`, hierarchical, `@path` imports. Reads `AGENTS.md` only via import or symlink (H). | `.claude/skills/<n>/SKILL.md` and `~/.claude/skills`, Agent Skills format. Plugins via `.claude-plugin/plugin.json` and marketplaces (H). The regenrek Claude provider is VERIFIED. | `.mcp.json` (project), `~/.claude.json`, `claude mcp add` (H) | Yes, the richest set (H). Events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStop`, `SessionStart`, `SessionEnd`, `PreCompact`, `Notification`. Configured in `.claude/settings.json` or a plugin `hooks/hooks.json`. Exit code 2 or a JSON `decision` blocks the action. A `Stop` hook can refuse completion until gates pass. |
| **Codex CLI** | `AGENTS.md`, hierarchical from repo root to cwd, plus `~/.codex/AGENTS.md` (H) | Agent Skills in `.agents/skills` / `~/.codex/skills` (M). Plugins via `.codex-plugin/plugin.json` plus `.agents/plugins/marketplace.json`; the paths are VERIFIED from regenrek codex.ts. | `~/.codex/config.toml` `[mcp_servers.<name>]`; `codex mcp add` (H) | Partial. `notify` exists (H). A hooks system with lifecycle events was added in 2026 (L). regenrek emits no hooks for Codex (VERIFIED), which suggests the support was immature or absent at v2.0. |
| **Cursor** | `.cursor/rules/*.mdc` (frontmatter `description`, `globs`, `alwaysApply`), `AGENTS.md` (H) | Plugins via `.cursor-plugin/plugin.json` (VERIFIED regenrek). Native Agent Skills support (L to M). | `.cursor/mcp.json`, `~/.cursor/mcp.json` (H) | Yes, `.cursor/hooks.json` (M to H): `beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`, `afterFileEdit`, `beforeSubmitPrompt`, `stop`. regenrek emits Cursor hooks (VERIFIED). |
| **GitHub Copilot coding agent** (and VS Code agent mode) | `.github/copilot-instructions.md`; `.github/instructions/*.instructions.md` with `applyTo`; `AGENTS.md` (H) | Agent Skills in `.github/skills` (M) | Coding agent: JSON in repository settings (H). VS Code: `.vscode/mcp.json` (H). Environment via `.github/workflows/copilot-setup-steps.yml` (H). | Limited (L to M). Copilot CLI and coding-agent hooks (`.github/hooks/*.json`, pre and post tool) may exist. The dependable gate surface is CI and required checks on the PR (H). |
| **Gemini CLI** | `GEMINI.md`, hierarchical; `context.fileName` can be set to `AGENTS.md` (H) | Extensions via `gemini-extension.json`, bundling MCP plus context (H). Agent Skills support (L to M). | `.gemini/settings.json` `mcpServers` (H) | Hooks added around late 2025 (M), e.g. `BeforeTool`, `AfterTool`, `BeforeAgent`, `AfterAgent`, configured in settings.json. Exact event names are uncertain. |

The common denominator across all five is that an **MCP stdio server works everywhere** (H). After that come `AGENTS.md` (native in Codex, Cursor and Copilot, optional in Gemini, via import in Claude) and Agent Skills `SKILL.md` (widely adopted, but install paths differ). Hook formats are **not** standardized.

---

## 5. Decision: should RepoMarshal generate agent-specific config itself?

**Recommendation (INFERRED): do not build a general projection or compiler engine.** Instead:

1. **Primary integration is a host-neutral MCP stdio server plus a CLI with identical JSON output.**
   - Target spec 2026-07-28, with fallback to 2025-11-25 because SDKs and clients lag.
   - Tools should be read-only with `outputSchema` and `structuredContent`, take an explicit `repo_path` argument (Roots is deprecated), and use explicit `plan_id` handles (stateless).
   - Expose plan artifacts as resources.
2. **Emit exactly one canonical, standards-based package** and let others project it:
   - Agent Skills `SKILL.md` (agentskills.io; stable, and the base format for both regenrek and MCP SEP-2640);
   - an Agent Plugins v1 `plugin.json` and `mcp.json` (Working Draft; pin `$schema` 1.0.0).
   - regenrek/agentrig can then project the package into Claude, Codex and Cursor plugins.
   - Optionally serve the same skill over MCP using `io.modelcontextprotocol/skills`, which is cheap, but client uptake is thin today.
3. **Provide at most a tiny `repomarshal init --print <host>` that *prints* MCP and hook snippets** for Claude, Codex, Cursor, Copilot and Gemini. It should not write or own vendor files.
   - This follows doidor's own conclusion ("author just the native adapter(s) … avoid a compiler", AGENT-RIG.md:119).
   - It also avoids a race with an ecosystem that has already pivoted twice in 2026: doidor dropped its CLI, and regenrek hard-cut to v2.
4. **Hooks for gates** need per-host thin shims, because no standard exists. Claude Code `Stop`/`PreToolUse` and Cursor `stop`/`beforeShellExecution` are the reliable targets (M to H). For Copilot, use CI and required checks.
5. **Read, don't own, existing policy surfaces.** Ingest `AGENTS.md` (`## Commands` gives Build/Test/Lint, following doidor's contract), `.agents/rules` globs and priority, `.cursor/rules` globs and `.github/instructions` `applyTo`. These are natural inputs for change-aware rule and skill selection.

UNKNOWN:
- whether agent-plugins.org 1.0.0 will reach a stable release;
- the actual 2026-10 Skills-over-MCP support in Claude Code, Cursor and Copilot (the matrix may lag);
- the exact hook schemas for Codex, Copilot and Gemini at this date.
