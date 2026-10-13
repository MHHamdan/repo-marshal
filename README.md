# RepoMarshal

**Marshal every AI-generated change from context to proof.**

RepoMarshal looks at the change a coding agent made: the git diff, including uncommitted work. From
that it compiles a **Change Plan**: which context the agent should read, which skills apply, and
which checks this specific change requires. Every check the plan skips comes with a stated reason.
RepoMarshal then runs the required checks itself and returns a verdict (PASS, FAIL, UNKNOWN or
BLOCKED) that never treats "could not check" as "passed".

> Status: **v0.1 development slice.** Local CLI only, Python ≥ 3.11, no runtime dependencies.
> Not yet published to PyPI.

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
- **Gates.** These are inferred, on an unconfigured repository, from `package.json` scripts and Python
  tool config (ruff, mypy, pyright, pytest), per directory scope, plus two native baseline checks:
  Markdown relative links, and secret patterns in added lines.
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
- **A local PASS is advisory.** The coding agent runs as your user and could, with deliberate effort,
  tamper with anything local. The authoritative verdict is a re-run of `repomarshal done` where the
  agent has no write access (CI or a reviewer).
- **RepoMarshal is not a sandbox and does not enforce tool permissions.** `plan` never runs
  repository code. `done` runs the repository's own commands, just like running its tests yourself.
- **Test quality is not measured.** RepoMarshal proves which commands ran against which exact
  content, and how they exited. It does not prove that the tests are meaningful.
- **Not covered by the digest:** ignored files such as `.env` and build output.

See [`docs/security/threat-model.md`](docs/security/threat-model.md).

## Integrations

| Integration | Status |
|---|---|
| Command gates (package.json scripts, ruff, mypy, pyright, pytest, `[gates.*]`) | Supported |
| Native docs-links and secrets-diff baseline checks | Supported |
| MCP server, Claude Code Stop hook | Planned |
| NVIDIA SkillSpector (skill audit), Playwright JSON reporter (runtime) | Planned |
| Reticle, AgentProof, Chisel spec reader, UI Skills, UI/UX Pro Max | Planned (optional) |

Planned integrations have no code yet. A required gate without a provider is reported as
UNAVAILABLE, and the verdict is never PASS.

## Roadmap

See [`docs/roadmap.md`](docs/roadmap.md). Next up: test-tamper signals, the remaining catalogue
signals, a CI recipe, `init`/`doctor`, the Claude Code Stop hook and the MCP server.

## Development

```bash
uv venv -p 3.11 .venv && uv pip install -p .venv/bin/python pytest jsonschema ruff
.venv/bin/python -m pytest
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

## License

Apache-2.0. See [`LICENSE`](LICENSE). Upstream projects studied are listed, with their licenses, in
[`docs/UPSTREAM_PROVENANCE.md`](docs/UPSTREAM_PROVENANCE.md). No upstream code is included.
