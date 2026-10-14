# Field evaluation on real repositories

_MISSION.md §43. Measured on 2026-10-13/14 with RepoMarshal at `9cf7d64` (branch `v03/field-test`).
Every number below comes from the commands in §8. Nothing is estimated except the context sizes,
which RepoMarshal itself labels `bytes/4`._

## 1. Summary

27 real commits from 5 unconfigured open-source repositories (2 Python, 1 JavaScript, 2 TypeScript,
one of them a pnpm-style monorepo), each replayed as a change against its parent commit.

| Metric | Result |
|---|---|
| Gate selection, precision (selected and needed / selected) | **0.800** (68 / 85) |
| Gate selection, recall (selected and needed / needed) | **0.944** (68 / 72); all 4 misses are `dep_audit` |
| Cases that needed no expensive gate and got none | **15 / 21** (71%) |
| Cases that selected at least one unnecessary expensive gate | 7 / 27 |
| Plans that can never reach PASS (an UNAVAILABLE decision) | 9 / 27; 7 of them only because of a false positive |
| Real CI checks inventoried with the right command | **5 / 19**; 6 wrong command, 8 missing |
| `plan` wall time, 135 runs | median **0.184 s**, max **0.281 s** (CLI start-up alone: median 0.086 s) |
| Context, selected / candidate (estimated tokens) | 731,990 / 3,159,100 (**23.2%**) |

The four Markdown-only docs changes required only the docs-links check, and 6 of the 10 source or
test-only changes were planned exactly as judged. The errors cluster in four places: dependency
changes (no `dependency.changed` signal exists yet), CI-only changes (elevated risk demands
integration tests that no repository has), `.tsx` test files (treated as UI), and the gate inventory
(commands that rewrite files, ignore configuration, or use the wrong package manager; monorepo
packages and Makefiles not read). Each one has a reproduction (§6). All 14 are fixed in v0.3; §9 re-measures the same 27 cases
after the fixes (recall 1.000, precision 0.66 against the original judgment, about 0.80 when gates
the old inventory could not see are counted by the same rules). §10 measures the v0.6 precision
fixes on the same cases: precision 0.661 → 0.727 against the frozen judgment (0.771 → 0.848
rule-adjusted, now computed by `field_eval.py score --adjusted`), recall still 1.000.

## 2. Method

1. **Repositories.** Five popular, actively maintained public repositories, small to medium, in
   the mix the brief asks for. Each was cloned read-only (`--depth 200`) into
   `/tmp/claude-0/field/<name>`. Nothing was pushed or modified upstream.
2. **Cases.** 4–6 recent real commits per repository, chosen to cover docs-only, source, test-only,
   dependency, CI or config and, where the repository has one, frontend changes. Each case is
   replayed with HEAD detached at the commit and `--base <commit>^`, so the change is exactly
   that commit's diff.
3. **Runs.** For each case, `repomarshal plan --json --base <commit>^` five times (timed with
   `time.perf_counter` around the whole subprocess, interpreter start-up included), then
   `repomarshal done --dry-run --json --base <commit>^` once. No repository code was executed:
   `plan` only reads files and runs `git`, and `--dry-run` runs no gate. All tools a gate names
   (`npm`, `pnpm`, `ruff`, `mypy`, `python`) were on PATH, so availability never masked a decision.
4. **Judgment.** For each case I wrote down the gate decisions a careful reviewer would require
   for that diff, from the diff itself and the repository's CI workflows and package scripts.
   They are judged against the inventory RepoMarshal actually found, so planner errors and
   inventory errors are counted separately (§4 and §5). A "selected" decision is REQUIRED or
   UNAVAILABLE, at gate level or kind level (a kind-level UNAVAILABLE is a demand the repository
   cannot meet). Rules used:
   - code or test change in a scope: that scope's lint, format, typecheck and unit gates;
   - docs change: `native:docs-links`;
   - CI, build-config or unclassified change: the product's conservative default (cheap and
     standard gates widened to every scope) plus the secrets baseline for CI files, but no
     integration, runtime or a11y gate, which cannot check a workflow file;
   - dependency change: unit plus a dependency audit (`dep_audit`, UNAVAILABLE here because no
     repository configures one);
   - shipped UI change (components, MDX content): runtime and a11y. A test file is not shipped UI.
   The expected sets are in `tests/field/field_eval.py` (`EXPECTED`) so they can be challenged.
   **Caveat:** one evaluator wrote them, and I had seen the signal summary of a first run before
   finishing them, so this is not a blind judgment.
5. **Inventory.** For each repository, the checks its CI actually runs were compared with the gate
   inventory in the plan JSON (`inventory[]`, read at BASE).

## 3. Repositories

| Repository | URL | HEAD SHA (cloned) | Last commit | Language / tooling | Size (files / lines) | License |
|---|---|---|---|---|---|---|
| click | https://github.com/pallets/click | `2247b35ea1c47c727d7a06e51fa280e12a863ff6` | 2026-10-04 | Python; uv, tox, ruff, mypy, pyright, pytest | 178 / 42,067 | BSD-3-Clause |
| rich | https://github.com/Textualize/rich | `9d8f9a372cc5916fd4781fec207ced7ddac2f08f` | 2026-06-23 | Python; poetry, Makefile, black, mypy, pytest | 553 / 136,572 | MIT |
| express | https://github.com/expressjs/express | `a419aa7350389a275ab45fde2e849ed1f02449ef` | 2026-10-09 | JavaScript; npm, eslint, mocha | 214 / 26,779 | MIT |
| zustand | https://github.com/pmndrs/zustand | `d7a5583cffd80af515f7dfb69583c95cbdc9e2ce` | 2026-09-29 | TypeScript; pnpm, prettier, tsc, eslint, vitest | 144 / 43,659 | MIT |
| zod | https://github.com/colinhacks/zod | `680d19f7d1e042e55934209caeb79bc843b94f44` | 2026-10-13 | TypeScript **monorepo** (`workspaces: packages/*`, 8 packages); nub, biome, vitest | 742 / 167,010 | MIT |

Sizes are `git ls-files | wc -l` and the line count of those files. None of the repositories has
a `repomarshal.toml`.

## 4. Per-case results

`S` = selected (REQUIRED or UNAVAILABLE). Gate ids without a prefix are kind-level decisions
(no gate of that kind exists). `D` = `native:docs-links`, `sec` = `native:secrets-diff`,
`L/F/T/U` = the root scope's lint, format, typecheck and unit gates.

### 4.1 Signals and decisions

| # | Case (commit, subject) | Type | Files | Signals (file count) | Risk | Selected | False positive | False negative |
|---|---|---|---|---|---|---|---|---|
| 1 | click `fa99fbd` Use the `kbd` role ... in the docs | docs | 3 | docs.changed(3) | low | D | – | – |
| 2 | click `fc518e4` Fix race condition on `KeyboardInterrupt` ... | source+test+docs | 4 | code(2), docs(2), tests(1) | standard | D L T U | – | – |
| 3 | click `b8a4486` Split out types tests. | test-only (move) | 13 | code(13), tests(13) | standard | L T U | – | – |
| 4 | click `0c9e836` Update all GitHub actions, fix thread locking | CI | 6 | ci.changed(6) | elevated | L T U sec, integration | integration | – |
| 5 | click `3b16957` Make `prompt`/`ParamType` typing work without ... `typing_extensions` | dependency+source | 9 | code(6), tests(4), docs(1), **coverage.unclassified(2)** | standard | D L T U | – | dep_audit |
| 6 | rich `ce51a2f` Update README URLs from master to main branch | docs | 18 | docs.changed(18) | low | D | – | – |
| 7 | rich `7ef2d05` fix inline code in table cells | source+test | 2 | code(2), tests(1) | standard | T U tests:unit | tests:unit | – |
| 8 | rich `905b397` Update tests/test_cells.py | test-only | 1 | code(1), tests(1) | standard | T U tests:unit | tests:unit | – |
| 9 | rich `eccb594` don't test 3.8 | CI | 1 | ci.changed(1) | elevated | T U tests:unit sec, integration | tests:unit, integration | – |
| 10 | rich `285d9e9` drop 3.8 | python floor+source+changelog | 3 | build.config(1), code(1), docs(1) | standard | D T U tests:unit | tests:unit | – |
| 11 | express `53d4a0d` docs: fix capitalization ... in Readme | docs | 1 | docs.changed(1) | low | D | – | – |
| 12 | express `9a34acf` fix(res.send): preserve ETag generation ... | source+test | 2 | code(2), tests(1) | standard | L U | – | – |
| 13 | express `b4ab7d6` test: include edge case tests for `res.type()` | test-only | 1 | code(1), tests(1) | standard | L U | – | – |
| 14 | express `3b7e39f` deps: bump qs minimum to 6.16.0 | dependency | 1 | **coverage.unclassified(1)** | standard | L U | – | dep_audit |
| 15 | express `3ce6d0e` ci: add npm staged publication ... | CI | 1 | ci.changed(1) | elevated | L U sec, integration | integration | – |
| 16 | express `2eae22b` chore: ensure safe config in the npmrc | config | 1 | build.config(1) | standard | L U | – | – |
| 17 | zustand `b57db4f` docs(create): document that state cannot be read ... | docs | 2 | docs.changed(2) | low | D | – | – |
| 18 | zustand `3febf8c` fix(persist): clearStorage() should invalidate ... | source+test | 2 | code(2), tests(1), **frontend.ui(1: the .test.tsx)** | standard | U, runtime, a11y | runtime, a11y | – |
| 19 | zustand `95d3f33` test(middleware/immer): add runtime tests ... | test-only | 1 | code(1), tests(1), **frontend.ui(1)** | standard | U, runtime, a11y | runtime, a11y | – |
| 20 | zustand `1f531ba` chore(deps): update dev dependencies | dependency+lockfile+CI+source | 7 | ci(1), code(4), tests(2), **frontend.ui(2 tests)**, **coverage.unclassified(2)** | elevated | U sec, integration, runtime, a11y | integration, runtime, a11y | dep_audit |
| 21 | zustand `f094eeb` ci: build the docs with pmndrs/docs@v4 | CI | 1 | ci.changed(1) | elevated | U sec, integration | integration | – |
| 22 | zod `34a626f` docs: tighten the z.locales tree-shaking note | docs (MDX, docs site) | 1 | docs(1), code(1), frontend.ui(1) | standard | D F L U, runtime, a11y | U | – |
| 23 | zod `680d19f` fix(v4): report a union option that only checks rejected | source+test | 2 | code(2), tests(1) | standard | F L U | – | – |
| 24 | zod `07917f4` test(v4): pin the lazy safeParse error's stack behavior | test-only | 1 | code(1), tests(1) | standard | F L U | – | – |
| 25 | zod `f6e1701` chore(deps): bump next to 15.5.25 and vite to 7.3.6 | dependency | 3 | **coverage.unclassified(3)** | standard | F L U | – | dep_audit |
| 26 | zod `9c21983` ci: stage at verbose so a failed OIDC token exchange says why | CI script | 1 | code(1), sensitive_identifier(1); **no ci.changed** | standard | F L U sec | – | – |
| 27 | zod `e54716c` docs(ecosystem): add @apical-ts/craft | frontend (docs site) | 1 | code(1), frontend.ui(1) | standard | F L U, runtime, a11y | U | – |

`done --dry-run` returned `UNKNOWN` (exit 2, "dry run: no gates were executed") in all 27 cases,
and `would_run` listed exactly the REQUIRED gate decisions of the plan.

### 4.2 Latency and context

| # | Case | `plan` median / max (s, 5 runs) | Context selected / candidate (est. tokens) |
|---|---|---|---|
| 1 | click `fa99fbd` | 0.152 / 0.188 | 11,984 / 55,588 |
| 2 | click `fc518e4` | 0.176 / 0.215 | 57,590 / 290,517 |
| 3 | click `b8a4486` | 0.188 / 0.206 | 11,970 / 120,782 |
| 4 | click `0c9e836` | 0.157 / 0.179 | 2,509 / 2,509 |
| 5 | click `3b16957` | 0.187 / 0.190 | 121,558 / 313,780 |
| 6 | rich `ce51a2f` | 0.217 / 0.226 | 91,632 / 91,632 |
| 7 | rich `7ef2d05` | 0.228 / 0.263 | 11,990 / 354,235 |
| 8 | rich `905b397` | 0.227 / 0.256 | 11,990 / 122,001 |
| 9 | rich `eccb594` | 0.223 / 0.232 | 6,002 / 6,002 |
| 10 | rich `285d9e9` | 0.229 / 0.246 | 54,910 / 356,143 |
| 11 | express `53d4a0d` | 0.155 / 0.170 | 2,592 / 2,592 |
| 12 | express `9a34acf` | 0.161 / 0.175 | 11,983 / 117,077 |
| 13 | express `b4ab7d6` | 0.174 / 0.184 | 11,953 / 100,582 |
| 14 | express `3b7e39f` | 0.163 / 0.170 | 682 / 682 |
| 15 | express `3ce6d0e` | 0.166 / 0.173 | 3,608 / 3,608 |
| 16 | express `2eae22b` | 0.166 / 0.196 | 18 / 18 |
| 17 | zustand `b57db4f` | 0.160 / 0.165 | 10,486 / 35,861 |
| 18 | zustand `3febf8c` | 0.158 / 0.159 | 11,944 / 79,789 |
| 19 | zustand `95d3f33` | 0.151 / 0.160 | 11,262 / 64,050 |
| 20 | zustand `1f531ba` | 0.196 / 0.212 | 78,484 / 130,396 |
| 21 | zustand `f094eeb` | 0.148 / 0.170 | 6,924 / 6,924 |
| 22 | zod `34a626f` | 0.209 / 0.234 | 11,813 / 57,175 |
| 23 | zod `680d19f` | 0.219 / 0.240 | 46,700 / 428,239 |
| 24 | zod `07917f4` | 0.222 / 0.249 | 11,999 / 270,101 |
| 25 | zod `f6e1701` | 0.219 / 0.231 | 109,537 / 119,896 |
| 26 | zod `9c21983` | 0.234 / 0.245 | 7,938 / 7,938 |
| 27 | zod `e54716c` | 0.239 / 0.281 | 11,932 / 20,983 |

Machine: 4 vCPU Linux container, CPython 3.11.17, warm page cache (clones on local disk).

## 5. Gate inventory against each repository's CI

| Repository | What CI runs | Inventoried gate (argv) | Verdict |
|---|---|---|---|
| click | `tox` (pytest) | `root:unit` `python -m pytest -q` | correct (honours `addopts`) |
| | pre-commit `ruff-check` | `root:lint` `ruff check .` | **wrong command:** `[tool.ruff] fix = true`, so it rewrites files |
| | pre-commit `ruff-format` | – | **missing** (no Python format source) |
| | `tox -e typing`: `mypy` | `root:typecheck` `mypy .` | **wrong command:** `.` overrides `[tool.mypy] files = ["src", "tests/typing"]` |
| | `tox -e typing`: `pyright --verifytypes` | – | **missing** (one typecheck gate per scope; mypy wins) |
| rich | `make format-check` (black) | – | **missing** (Makefile targets are not read) |
| | `make typecheck` (`mypy -p rich`) | `root:typecheck` `mypy .` | **wrong command:** `.` overrides `files = ["rich"]` |
| | `pytest tests` | `root:unit` `python -m pytest -q` | correct; but `tests/pytest.ini` adds a duplicate `tests:unit` |
| express | `npm run lint` | `root:lint` `npm run lint` | correct |
| | `npm run test-ci` (nyc + `npm test`) | `root:unit` `npm run test` | correct |
| zustand | `pnpm run test:spec` | `root:unit` `pnpm run test` (runs all `test:*`) | correct (superset) |
| | `test:lint`, `test:types`, `test:format` | – | **missing** as gates (`test:<kind>` names not recognised) |
| zod | `nub run format:check` | `root:format` `pnpm run format:check` | **wrong runner:** `packageManager` is `nub@0.8.3`; pnpm exits 1 ("This project is configured to use nub") |
| | `nub run lint:check` | `root:lint` `pnpm run lint` | **wrong command:** `lint` is `biome lint --write .`; also wrong runner |
| | `nub run test` | `root:unit` `pnpm run test` | **wrong runner** |
| | `--filter @zod/integration test:all`, `--filter @zod/resolution test:all` | – | **missing** (`packages/*` are not scopes) |

Totals over the 19 CI checks: 5 correct, 6 wrong command or runner, 8 missing; plus 1 spurious
gate (`rich tests:unit`). On the positive side, every repository got a usable plan with zero
configuration, every gate tool resolved on PATH, and no CI command was executed or trusted.

The `mypy .` and `ruff check .` claims were checked on a synthetic project, not on upstream code:
with `files = ["src"]`, `mypy` checked 1 file and `mypy .` checked 2 and failed; with
`fix = true`, `ruff check .` modified the file. The pnpm claim was checked the same way with a
synthetic `package.json`.

## 6. Findings and reproductions

Each finding has a fixture under `tests/fixtures/field/<case>/` (synthetic files; the fixture
README names the upstream repository and commits) and a test in
`tests/field/test_field_findings.py` marked `fieldfinding`, asserting the correct behaviour. All 14
failed at `9cf7d64`; all 14 pass after the v0.3 fixes (§9).

| Test | What is wrong | Seen in | Suspected root cause |
|---|---|---|---|
| `test_ruff_lint_gate_does_not_apply_fixes` | `ruff check .` applies fixes when the config sets `fix = true`: a lint failure is silently fixed and `done` reports UNKNOWN (tree modified) | click | `inventory.py` `_python_gates` (argv) |
| `test_mypy_gate_respects_configured_files` | `mypy .` overrides `[tool.mypy] files`, so strict mypy checks tests/docs/examples the project never checks: false FAIL | click, rich | `inventory.py` `_python_gates` (argv) |
| `test_lint_gate_prefers_the_non_mutating_check_script` | `lint` (`--write`) chosen over `lint:check` | zod | `inventory.py` `SCRIPT_KINDS` |
| `test_package_manager_field_selects_the_runner` | runner taken from the lockfile, `packageManager` ignored; pnpm refuses to run | zod | `inventory.py` `_package_manager` |
| `test_test_prefixed_scripts_are_inventoried` | `test:lint`, `test:types`, `test:format` not recognised | zustand | `inventory.py` `SCRIPT_KINDS` |
| `test_makefile_targets_are_inventoried` | Makefile targets (architecture §3.2) not read | rich | `inventory.py` (source not implemented; `Makefile` only makes a scope) |
| `test_pytest_ini_inside_tests_dir_does_not_duplicate_the_unit_gate` | `tests/pytest.ini` makes `tests/` a scope; the suite is required twice | rich | `inventory.py` `detect_scopes` |
| `test_workspace_packages_are_gate_scopes` | `packages/*/package.json` never becomes a scope; package scripts are invisible | zod | `inventory.py` `detect_scopes` (depth ≤ 1) |
| `test_dependency_bump_raises_dependency_changed` | a dependency bump is only `coverage.unclassified`; `dep_audit` not demanded | express, click, zustand, zod | `signals.py` `CATALOGUE` (`dependency.changed` is on the roadmap, not implemented) |
| `test_lockfile_change_raises_dependency_changed` | `pnpm-lock.yaml` / `uv.lock` are `coverage.unclassified` | zod, click, zustand | `signals.py` `CATALOGUE` |
| `test_tsx_test_file_does_not_require_runtime_or_a11y` | a `.test.tsx` fires `frontend.ui`; runtime and a11y become UNAVAILABLE, so `done` can never PASS | zustand | `signals.py` `_is_frontend` |
| `test_ci_workflow_only_change_does_not_demand_integration_tests` | CI-only change → elevated → integration UNAVAILABLE in every repository; `done` can never PASS, for a reason that cannot verify a workflow | click, rich, express, zustand | `decisions.py` `_triggers` (elevated → integration); rule-level, see §7 |
| `test_script_run_by_ci_under_dot_github_is_ci_changed` | `.github/scripts/*.sh` run by the release workflow is not `ci.changed` | zod | `signals.py` `CI_PREFIXES` |
| `test_lockfile_is_not_full_text_context` | changed lockfiles are P1 context at full size (108k, 65k, 45k est. tokens) | zod, click, zustand | `context.py` (P1 always includes changed files) |

Regression guards (unmarked, passing): docs-only change requires only `native:docs-links`; a
Python source change requires lint, typecheck and unit and nothing expensive; tests moved into new
files raise no tamper signal; a JS source+test change requires lint and unit; `.npmrc` widens to
lint and unit; a dependency-only `package.json` edit is not `build.config_changed`; a `.test.ts`
change in the monorepo requires format, lint and unit only; a docs-site component change reports
runtime and a11y as UNAVAILABLE; an added line mentioning a token requires the secrets baseline.

## 7. Discussion

**What worked.** The diff-driven core is sound on ordinary changes. The four Markdown-only docs
changes required the docs-links check and nothing else. The source and test-only changes in click,
express and zod (6 cases) were planned exactly as judged. The click test split (a 438-line deletion
moved into eight new files) raised no false `tests.deleted`. Recall of required gates was 0.944, and
every miss is one not-yet-implemented signal. `plan` stays under 0.3 s on every case, about half of
it interpreter start-up, so latency is not a concern at this size. For source and test-only changes
the context selection is effective: 3–20% of candidate tokens.

**Where precision is lost.** 17 false positives, in three groups:
- *CI-only changes (5).* `ci.changed` is elevated, and elevated risk demands integration tests.
  None of the five repositories has an integration gate, so every CI-only change becomes UNKNOWN
  with the reason "no integration gate or provider configured". This follows architecture §4.2 as
  written, so it is a rule-level question for the lead. If an un-PASSable CI change is the intent
  (CI files cannot be verified locally), the reason should say that rather than naming
  integration tests. If it is not the intent, elevated risk from `ci.changed` should not trigger
  integration.
- *`.tsx` test files (6 gate decisions over 3 cases).* zustand renders hooks in `.test.tsx` files.
  The extension rule makes them UI, so a one-line library fix demands runtime and a11y checks.
- *Scopes (6).* rich's `tests/pytest.ini` doubles the unit gate (4 cases). In zod, the root
  `vitest` gate is required for edits inside the Next.js docs site (2 cases), because no
  `packages/*` scope exists. The MDX case (#22) also demands runtime and a11y for one prose
  sentence. I counted that as correct, because the architecture deliberately treats `.mdx` as
  code and UI (MDX can embed components), but it is debatable.

**Dependencies are the largest gap.** In all four dependency changes, the manifests and lockfiles
(8 files) get only `coverage.unclassified`, never a dependency signal. The fallback is safe (it
widens to lint and unit), but no dependency audit is ever demanded, and the plan explains a `qs`
bump as "file matched no signal rule". `dependency.changed` is a documented v0.1 signal that is
still on the roadmap, so this measures known work rather than a regression.

**The inventory is the weakest part on real repositories.** Only 5 of 19 CI checks were found
with a command that would behave like CI. Three of the wrong commands would produce a misleading
verdict, not just a gap:
- click's and zod's lint gates rewrite files, so `done` reports UNKNOWN and a real lint error is
  auto-fixed out of sight;
- `mypy .` on click and rich would very likely fail on files the projects never type-check (a
  false FAIL);
- every zod gate would exit 1 under pnpm (a false FAIL on any change).

The planner cannot compensate for these. Reading `packageManager`, preferring `*:check` scripts,
passing `--no-fix`, and running `mypy` without a path when `files` is set are each small changes.
Monorepo scopes (`workspaces`, `pnpm-workspace.yaml`) and Makefile targets are larger ones.

**Verdict honesty held.** No plan claimed PASS-able coverage it did not have, and missing
providers were reported as UNAVAILABLE. The cost is that 9 of 27 plans could never PASS, 7 of
them because of the false positives above. On a real team that rate would push people to turn the
hook off, which is the over-checking failure the product is meant to prevent.

**Limitations.**
- One evaluator wrote the expected gates, not blind.
- 27 cases over 5 repositories, with no very large repository: none above 742 files, so the
  latency numbers say nothing about large monorepos.
- Commits were replayed as whole commits. Agents usually produce smaller, uncommitted diffs.
- Only planning was evaluated. No gate ran, so the inventory verdicts above are reasoned from the
  commands and from synthetic checks (§5), not observed on upstream code.

**Side observation, not filed.** While building the fixtures, a working-tree file whose content
changed but whose size and mtime matched the index (here, a copy with preserved timestamps) made
`plan` stop with `git diff-tree: expected 1 patches, got 0`. The CLI reports it as BLOCKED, so it
fails closed. Git's diff reads the stat-clean "old" blob from the working tree. The test harness
avoids it by not preserving mtimes. It is worth hardening, but no field repository triggered it.

## 8. Reproduce

```bash
mkdir -p /tmp/claude-0/field && cd /tmp/claude-0/field
for r in pallets/click Textualize/rich expressjs/express pmndrs/zustand colinhacks/zod; do
  git clone -q --depth 200 "https://github.com/$r.git" "${r#*/}"
done
git -C click checkout -q 2247b35ea1c47c727d7a06e51fa280e12a863ff6   # and the other SHAs in §3

cd /home/user/wt-field-test
# One case by hand (HEAD at the commit, BASE = its parent):
git -C /tmp/claude-0/field/express checkout -q --detach 3b7e39f
( cd /tmp/claude-0/field/express &&
  PYTHONPATH=/home/user/wt-field-test/src /home/user/repo/.venv/bin/python -m repomarshal plan --json --base 3b7e39f^ &&
  PYTHONPATH=/home/user/wt-field-test/src /home/user/repo/.venv/bin/python -m repomarshal done --dry-run --json --base 3b7e39f^ )

# All 27 cases (5 timed plan runs + 1 dry run each; restores every clone's HEAD), then the scores:
/home/user/repo/.venv/bin/python tests/field/field_eval.py run /tmp/claude-0/field /tmp/claude-0/field-out
/home/user/repo/.venv/bin/python tests/field/field_eval.py score /tmp/claude-0/field-out

# Findings and regression guards:
/home/user/repo/.venv/bin/python -m pytest -o addopts="" -q tests/field
```

Signals, decisions and context sizes are deterministic and reproduce exactly. Latency varies with
the machine.

## 9. After the v0.3 fixes

The same 27 cases were re-run on 2026-10-14 with `claude/v0.3` after the fix-inv and fix-sig
merges (`field_eval.py run` + `score`, same clones, same SHAs). The judgment in `EXPECTED` was
**not changed**, so this is scored against the original, frozen expectations.

| Metric | `9cf7d64` (before) | after fixes |
|---|---|---|
| Recall | 0.944 (68 / 72) | **1.000** (72 / 72): the four `dep_audit` misses are now demanded via `dependency.changed` |
| Precision, against the frozen judgment | 0.800 (68 / 85) | 0.661 (72 / 109) |
| Cases selecting an unnecessary expensive gate | 7 / 27 | 6 / 27 |
| `plan` wall time, 135 runs | median 0.184 s, max 0.281 s | median 0.206 s, max 0.313 s |
| Context, selected / candidate | 23.2% | 16.6% (lockfiles are no longer full-text context) |

Precision fell because the inventory now finds more of each repository's real checks: of the 37
"false positives", 15 are format, lint or typecheck gates that the old inventory missed (rich
`root:format`, zustand `root:lint` / `root:typecheck` / `root:format`) in a scope with a code or
test change. The stated rule in §2 ("that scope's lint, format, typecheck and unit gates") requires
them; the frozen judgment does not list them only because they did not exist when it was written.
Counting those 15 as needed gives a precision of 87 / 109 = **0.80**. The rest are real
over-selection:

- **Workspace duplicates (8, zod).** Package-scope gates (`packages/zod:unit`, `packages/tsc:lint`,
  ...) now exist, and a root change requires them too, although the root `test` script already
  runs the whole workspace. Cost, not safety: the same tests run twice.
- **Integration on dependency changes (5) and on one elevated change (1).** A deliberate rule
  (`decisions.py`: integration is demanded for `security.auth_path`, `db.migration` and
  `dependency.changed`), because an upgraded dependency can break behaviour only an integration
  test exercises. The judgment disagrees. In these repositories it is UNAVAILABLE, but so is
  `dep_audit`, so the verdict was already UNKNOWN without it; a maintainer waiver (v0.3) is the
  way through.
- **Secrets baseline on lockfile changes (4).** Cheap; kept.
- **`root:unit` for docs-site content (2, zod)** and `root:format` on a dependency-only change
  (1, rich).

Latency rose by about 20 ms per plan, mostly from reading workspace packages and Makefiles in the
larger repositories; the webapp fixture is unchanged (median 177 ms after a lazy import in `api`).
Zod's gates show as UNAVAILABLE in this re-run only because `nub` (its `packageManager`) is not on
this machine's PATH; availability does not change which gates are selected. The §5 comparison of
the inventory against each repository's CI was not redone by hand; each of its 8 findings has a
passing test.

## 10. After the v0.6 precision fixes

Re-run on 2026-10-14 on branch `v06/precision`, same 27 cases, same clones (HEADs as in §3, clean
afterwards), same SHAs, and the frozen `EXPECTED` (not edited). "Before" is the worktree's starting
commit `25d89cf`, run from a `git archive` copy of it; "after" is this branch. Both were scored with
the same `field_eval.py score OUT --adjusted`, which prints the frozen score and the rule-adjusted
score. The three fixes:

1. **Workspace coverage** (architecture §3.5). A workspace package's script gate is SKIPPED as
   "covered by root:<kind>" when there is explicit evidence, read at BASE and confirmed in the
   working tree, that the root script of the same kind already runs its checks: a recursive
   workspace runner of the same script name (`pnpm -r`, `nub run -r`, `npm --workspaces`,
   `yarn workspaces`, `turbo run`, `lerna run`, with no filter), or a root `vitest`/`jest` run
   whose root config lists the package in `projects` while the package script is the bare
   runner. No evidence keeps both gates. In zod, `vitest.config.ts` has
   `projects: ["packages/*", "./vitest.compile.config.ts"]` and `packages/zod` / `packages/mini`
   run plain `vitest run`, so both are covered by `root:unit`. `packages/tsc:lint` runs `tslint`,
   which the root `biome lint .` does not run, so it stays.
2. **Secrets on lockfile-only changes.** `dependency.changed` no longer triggers
   `native:secrets-diff` when every file that raised it is a lockfile (both ends of a rename)
   whose added lines are fully visible and carry no `scheme://user:pass@` URL; the decision says
   so ("dependency.changed comes only from lockfiles (...); a lockfile-only change does not require
   secrets"). Manifests, `requirements*.txt`, a binary (`bun.lockb`) or truncated lockfile diff,
   and every other trigger still require it.
3. **No integration for a dependency bump alone.** `dependency.changed` and `task.dependencies`
   no longer trigger integration. `dep_audit` and the widened `unit` gates stay; auth paths,
   migrations, `task.security`, `task.data`, test-tamper signals and elevated risk with code still
   demand integration.

```bash
git archive 25d89cf src tests/field/field_eval.py | tar -x -C "$BEFORE_TREE"
python "$BEFORE_TREE/tests/field/field_eval.py" run /tmp/claude-0/field "$OUT/before"
python tests/field/field_eval.py run /tmp/claude-0/field "$OUT/after"
python tests/field/field_eval.py score "$OUT/before" --adjusted
python tests/field/field_eval.py score "$OUT/after" --adjusted
```

| Metric | `25d89cf` (before) | v0.6 (after) |
|---|---|---|
| Precision, frozen judgment | 0.661 (72 / 109) | **0.727** (72 / 99) |
| Precision, rule-adjusted (`score --adjusted`) | 0.771 (84 / 109) | **0.848** (84 / 99) |
| Recall | 1.000 (72 / 72) | 1.000 (72 / 72) |
| Cases selecting an unnecessary expensive gate | 6 / 27 | **3 / 27** |
| `plan` wall time, 135 runs, median / max (run A) | 0.251 s / 0.402 s | 0.267 s / 0.631 s |
| `plan` wall time, 135 runs, median / max (run B) | 0.236 s / 0.379 s | 0.245 s / 0.440 s |
| Context, selected / candidate | 16.6% | 16.6% |

False positives by kind went from integration 6, unit 8, secrets 4, format 8, lint 6,
typecheck 4, dep_audit 1 to integration **2**, unit **2**, secrets 4, format 8, lint 6,
typecheck 4, dep_audit 1. The 10 removed are integration on the dependency changes click
`3b16957`, rich `285d9e9`, express `3b7e39f` and zod `f6e1701` (4), and the six workspace package
unit gates in zod `680d19f`, `07917f4`, `f6e1701` and `9c21983` (`packages/zod:unit` 4 times,
`packages/mini:unit` twice). The three cases that no longer select an unnecessary expensive gate
are click `3b16957`, express `3b7e39f` and zod `f6e1701`.

**Latency.** Runs A (before, then after) and B (after, then before) were taken back to back on
the same 4 vCPU container, which was noisier than in §4 (the same `25d89cf` code measured
0.290 s median earlier in the session). Outside zod the medians are within 6 ms
(0.230 → 0.236 s, 0.218 → 0.223 s). On zod, coverage reads the root vitest config and the
package scripts in both trees: 0.332 → 0.386 s (A) and 0.318 → 0.344 s (B), i.e. about 25–55 ms.

**The rule-adjusted score.** `--adjusted` counts a selected gate that `EXPECTED` does not list as
needed when its kind is format, lint or typecheck and a `code.changed` or `tests.changed` file
lies in its scope (the §2 rule "that scope's lint, format, typecheck and unit gates", applied to
gates the 9cf7d64 inventory could not see). It gives 12 such gates in both runs (rich
`root:format` in `7ef2d05`, `905b397`, `285d9e9`; zustand `root:format/lint/typecheck` in
`3febf8c`, `95d3f33`, `1f531ba`). §9 counted 15 by hand (0.80). Its own stated rule gives 12;
the 3 extra are most likely zustand `f094eeb`'s three widened gates, a CI-only change with no
code or test file, which the stated rule does not cover. The reproducible number for the same
before-state is 0.771.

**Why secrets did not move.** §9 described the 4 secrets false positives as "secrets baseline
on lockfile changes". None of the 4 is lockfile-only: click `3b16957` changes `pyproject.toml`
(and `uv.lock`), rich `285d9e9` and express `3b7e39f` change only a manifest, and zod `f6e1701`
changes two `package.json` files next to `pnpm-lock.yaml`. Fix (b) is therefore exercised by the
unit tests and the zod `59bbc03`-style fixture
(`test_lockfile_only_change_requires_neither_secrets_nor_integration`), not by these 27 cases. A real `pnpm-lock.yaml` bump is usually
larger than the 2,000-line diff cap; a truncated lockfile diff still requires the secrets gate
(fail closed), so on large bumps the exemption will often not apply.

### Remaining false positives (27, against the frozen judgment)

| Case | Gate | Why it is still selected |
|---|---|---|
| rich `7ef2d05`, `905b397`, `285d9e9` | `root:format` | Needed by the §2 rule (`make format-check`, found since v0.3); the frozen judgment predates it. Counted as needed by `--adjusted`. |
| zustand `3febf8c`, `95d3f33`, `1f531ba` | `root:format`, `root:lint`, `root:typecheck` (9) | Same: `test:format`, `test:lint`, `test:types`, found since v0.3. Counted by `--adjusted`. |
| rich `eccb594`, zustand `f094eeb` | `root:format` (1); `root:format/lint/typecheck` (3) | CI-only changes widen the cheap and standard gates. §2's CI rule ("the product's conservative default ... widened to every scope") requires them, and `EXPECTED` already lists the widened `T U` / `U` for these cases, so I believe the judgment is wrong here; `--adjusted` does not count them because no code or test file changed. |
| click `3b16957`, express `3b7e39f`, zod `f6e1701` | `native:secrets-diff` | A manifest changed (`pyproject.toml`, `package.json`). Kept by design: only lockfile-only changes are exempt. Cheap. |
| rich `285d9e9` | `dep_audit`, `native:secrets-diff` | `dependency.changed` fires on `[tool.poetry.dependencies] python = ">=3.9"`: Poetry keeps the interpreter floor in its dependency table. A real signal false positive; the fix is to discount the `python` key there (not done here, it is a signal change). |
| zustand `1f531ba` | `integration` | Not from the dependency: `ci.changed` makes risk elevated and `code.changed` fires, which demands integration since v0.3. A rule-level disagreement with the judgment, unchanged by v0.6. |
| zod `9c21983` | `integration` | Same rule: `.github/scripts/*.sh` is `ci.changed` (elevated) and a `.sh` file is `code.changed`. |
| zod `f6e1701`, `9c21983` | `packages/tsc:lint` | Widening (`dependency.changed`, `ci.changed`) reaches every scope. Its `tslint` script is not run by the root `biome lint .`, so there is no coverage evidence and the gate stays (fail closed). |
| zod `34a626f`, `e54716c` | `root:unit` | An `.mdx` page and a `.tsx` component under `packages/docs/`. The judgment calls the root unit gate unneeded, but `packages/docs` is a vitest project of the root run (`projects: ["packages/*"]`, `packages/docs/vitest.config.ts`) with its own tests (`content/api.test.ts` reads the docs' MDX). I think the judgment is debatable here; it is kept as written. |

Zod's gates are still UNAVAILABLE in these runs because `nub` is not on this machine's PATH; that
does not change which gates are selected.
