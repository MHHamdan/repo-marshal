# RepoMarshal

**Marshal every AI-generated change from context to proof.**

RepoMarshal looks at the change a coding agent made: the git diff, including uncommitted work. From
that it compiles a **Change Plan**: which context the agent should read, which skills apply, and
which checks this specific change requires. Every check the plan skips comes with a stated reason.
RepoMarshal then runs the required checks itself and returns a verdict (PASS, FAIL, UNKNOWN or
BLOCKED) that never treats "could not check" as "passed".

> Status: **v0.5 development slice.** CLI, Claude Code hooks, MCP server and a CI recipe;
> Python ≥ 3.11, no runtime dependencies. Not yet published to PyPI. Measured on 27 commits from
> 5 real repositories: [`docs/evaluation.md`](docs/evaluation.md).

## Why not just install the tools?

Scanners, browser runners and test frameworks are good at running checks. What none of them decides
is **which** checks a given change needs, or what the overall answer is when one of them could not
run. An agent either runs everything on every edit (slow, so people turn it off), or runs whatever
it picks and claims "done". RepoMarshal is the deterministic layer in between:

```text
change → signals → plan (required / skipped / unavailable, each with a reason) → run → verdict
```

## Quick start (from source)

```bash
git clone https://github.com/MHHamdan/repo-marshal && cd repo-marshal
uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python .
cd /path/to/your/repo
/path/to/repo-marshal/.venv/bin/repomarshal plan "what you are about to change"
/path/to/repo-marshal/.venv/bin/repomarshal done        # exit 0 PASS, 1 FAIL, 2 UNKNOWN, 3 BLOCKED
```

`plan --json` and `done --json` emit output that matches the schemas in `src/repomarshal/schemas/`.
`plan --explain` prints every reason for every gate.

### With a coding agent, and in CI

- **Claude Code:** `repomarshal init --print claude-code` prints a hooks snippet for
  `.claude/settings.json`.
  - **SessionStart** anchors BASE before the agent works.
  - **Stop** runs `repomarshal done --hook claude-code`. Any verdict other than PASS, and any
    error, signal or timeout, blocks the stop. The agent gets the failing or unknown gates as
    feedback.
  - **No endless loop.** If the agent stops again (`stop_hook_active`) with the *same* non-PASS
    verdict over the *same* evidence, the stop is allowed and you get a message that the change is
    **UNVERIFIED**, listing the open gates. A new or worse verdict always blocks again.
- **Any MCP client:** `pip install "repomarshal[mcp]"`, then register `repomarshal mcp` as a stdio
  server. Tools: `repomarshal_plan`, `repomarshal_done` and `repomarshal_explain`. Each takes an
  explicit `repo_path`.
- **CI, the authoritative verdict:** see [`docs/guides/ci.md`](docs/guides/ci.md). Run
  `repomarshal done --base <PR base sha>` where the agent cannot write. Better, use the v0.4 split
  (`repomarshal ci plan` / `ci gate` / `ci verdict`): each gate that runs the PR's code gets its
  own job with no token permissions, and the judge jobs run no PR code at all. v0.5: the verdict
  is posted as the commit status `repomarshal/verdict` by a workflow that always runs from the
  default branch (`repomarshal-verdict.yml`). Make that status the one required check, and post
  it as a GitHub App ([`docs/guides/ci.md`](docs/guides/ci.md#the-trusted-verdict-and-branch-protection-v05)).
- **Waivers, human only:** when a required gate cannot run, a maintainer can accept the gap for one
  change: in CI with a `repomarshal-waive:<gate>` PR label plus a reason comment, both made by a
  person (anything done through a GitHub App, such as a coding agent acting under the
  maintainer's account, is rejected); locally with `repomarshal waive <gate> --reason ...` at a
  terminal. A waiver
  never turns a FAIL into a PASS, counts only when the BASE `repomarshal.toml` sets
  `[completion] waivers_permit_pass = true`, and is recorded in the evidence. The Stop hook and the
  MCP server never accept waivers. See [`docs/guides/waivers.md`](docs/guides/waivers.md).
- **`repomarshal doctor`:** reports the detected gates, the policy, and the optional providers.
  `repomarshal init` writes a commented `repomarshal.toml` and touches nothing else.

## Example: three changes, three plans

This is real output on the repository's test fixture (`tests/fixtures/webapp`: a React frontend and a
Python backend, with **no RepoMarshal configuration**). The checks are stub tools that pass, so only
the planning and verdict logic is on display.

| Gate | A: edit `docs/install.md` | B: edit `LoginForm.tsx` + `login.css` | C: edit `backend/app/auth/refresh.py` |
|---|---|---|---|
| native:docs-links | **REQUIRED** | SKIPPED | SKIPPED |
| frontend:lint / typecheck / unit | SKIPPED | **REQUIRED** | SKIPPED |
| frontend:runtime, frontend:a11y | SKIPPED | **REQUIRED** | SKIPPED |
| backend:lint / typecheck / unit | SKIPPED | SKIPPED | **REQUIRED** |
| backend:integration | SKIPPED | SKIPPED | **REQUIRED** |
| native:secrets-diff | SKIPPED | SKIPPED | **REQUIRED** |
| security_scan | NOT_APPLICABLE | NOT_APPLICABLE | **UNAVAILABLE** |
| runtime for backend | — | — | **UNAVAILABLE** |
| risk / skill picked | low / docs-writing | standard / ui-forms | elevated / auth-security |
| `repomarshal done` | **PASS** (exit 0) | **PASS** (exit 0) | **UNKNOWN** (exit 2) |

Scenario C is UNKNOWN on purpose. An auth change requires a security scan and a runtime check, and
this repository has neither configured. RepoMarshal says so instead of passing:

```text
$ repomarshal done
TASK COMPLETION
  backend:lint               PASS     exit code 0
  native:secrets-diff        PASS     baseline check: no high-confidence secret pattern in 3 added line(s)
  backend:typecheck          PASS     exit code 0
  backend:unit               PASS     exit code 0
  backend:integration        PASS     exit code 0

OVERALL  UNKNOWN
  - security_scan (no gate): required but unavailable (no security_scan gate or provider configured for the changed files)
  - runtime (no gate): required but unavailable (no runtime gate or provider configured for the changed files)

evidence: .repomarshal/evidence/20261013T183349Z-fc65c711074c-a507ec7d.json

Local verdict: advisory for the agent loop. The authoritative verdict is a re-run of `repomarshal done` where the agent cannot write (CI or a reviewer).
```

Committing a `repomarshal.toml` that defines those two gates on the base branch turns them into
commands RepoMarshal runs, and the verdict can then be PASS:

```toml
version = 1
[gates.security]
kind = "security_scan"
scope = "backend/"
argv = ["bandit", "-r", "app"]

[gates.backend-runtime]
kind = "runtime"
scope = "backend/"
argv = ["python", "scripts/smoke.py"]
```

## What RepoMarshal decides, and how

- **Signals.** A versioned catalogue of deterministic rules over paths and *added* lines, such as
  `docs.changed`, `frontend.ui`, `security.auth_path`, `security.sensitive_identifier` and
  `build.config_changed`. A file no rule recognises raises `coverage.unclassified`, which *widens*
  the plan instead of shrinking it.
- **Gates.** These are inferred, on an unconfigured repository, from `package.json` scripts (run with
  the `packageManager` or lockfile's runner, preferring non-mutating `*:check` scripts), Python tool
  config (ruff without fixing, mypy, pyright, pytest) and Makefile targets, per directory scope and
  per workspace package. Plus two native baseline checks, Markdown relative links and secret
  patterns in added lines, and the SkillSpector and Playwright providers when installed.
  - Cheap and standard gates run whenever their scope has code changes.
  - Expensive gates (integration, runtime, a11y, security scans) run only when a signal requires
    them.
- **Context and skills.** Changed files and their paired tests are always included. Related docs and
  nearby files are added within a budget, with sizes labelled as `bytes/4` estimates. Skills are
  picked from `SKILL.md` files in the repository.
- **Verdict.**
  - FAIL if any required gate fails.
  - UNKNOWN if any required gate could not produce evidence: unavailable tool, timeout, nothing
    verified, or the working tree changed during the run.
  - BLOCKED if the policy is invalid.
  - Skipped gates never count as evidence.

Details: [`docs/architecture.md`](docs/architecture.md), [`docs/adr/`](docs/adr/).

## Security boundaries (read this)

- **The policy and gate definitions come from the BASE commit.** A change cannot weaken the checks
  that judge it.
- **A gate whose definition changed in the diff is UNKNOWN, whatever its exit code.** For example,
  editing `"test"` in package.json to `"exit 0"` cannot produce a PASS.
- **An explicit `--base` can only widen what is checked.** A repository nested, untracked, inside
  your project is refused.
- **A local PASS is advisory.** The coding agent runs as your user and could, with deliberate effort,
  tamper with anything local. The authoritative verdict is a re-run of `repomarshal done` where the
  agent has no write access (CI or a reviewer).
- **RepoMarshal is not a sandbox and does not enforce tool permissions.** `plan` never runs
  repository code. `done` runs the repository's own commands, just like running its tests yourself.
- **Test quality is not measured.** RepoMarshal proves which commands ran against which exact
  content, and how they exited. It does not prove that the tests are meaningful.
- **Not covered by the digest:** files ignored by the BASE commit's `.gitignore`, such as `.env` and
  build output. Ignore rules the agent controls (`.git/info/exclude`, global excludes, or a
  `.gitignore` edited in the change) are not trusted.
- **A gate's evidence is checked file by file:** the working tree is re-digested after every gate,
  and a gate that changes files gets an UNKNOWN result.

See [`docs/security/threat-model.md`](docs/security/threat-model.md).

## Integrations

| Integration | Status |
|---|---|
| Command gates (package.json scripts, Makefile targets, ruff, mypy, pyright, pytest, `[gates.*]`) | Supported |
| Native docs-links and secrets-diff baseline checks | Supported |
| Claude Code Stop hook (`repomarshal done --hook claude-code`) | Supported; tested against recorded hook payloads |
| MCP stdio server (`pip install "repomarshal[mcp]"`, `repomarshal mcp`) | Supported (MCP 2026-07-28, with 2025-11-25 fallback) |
| Hook and MCP config snippets for Codex, Cursor, Copilot and Gemini (`repomarshal init --print <host>`) | Experimental: printed only, untested on those hosts |
| NVIDIA SkillSpector (`skill_audit` when a skill changes; `skillspector` 2.12.x on PATH, `--no-llm`) | Experimental; tested against v2.12.0 |
| Playwright (`runtime`, JSON reporter; the project's own `@playwright/test`, `npx --no-install`) | Experimental; tested range 1.50–1.64 |
| Reticle, AgentProof, Chisel spec reader, UI Skills, UI/UX Pro Max | Planned (optional) |

Planned integrations have no code yet. A required gate without a provider is reported as
UNAVAILABLE, and the verdict is never PASS. Provider versions outside the tested range, zero tests
run, an incomplete scan, or no network for SkillSpector's OSV lookup all give UNKNOWN, never
PASS. The tool version is recorded in each gate result.

## Roadmap

See [`docs/roadmap.md`](docs/roadmap.md). Next up: evidence chaining and a `status` command,
workspace-aware deduplication of monorepo gates, `db.migration`, and publishing to PyPI.

## Development

```bash
uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python pytest jsonschema ruff
.venv/bin/python -m pytest
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

## License

Apache-2.0. See [`LICENSE`](LICENSE). Upstream projects studied are listed, with their licenses, in
[`docs/UPSTREAM_PROVENANCE.md`](docs/UPSTREAM_PROVENANCE.md). No upstream code is included.
