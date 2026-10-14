# Running `repomarshal done` in CI

A local `repomarshal done` is advisory. The coding agent runs as your user and can, with deliberate
effort, tamper with anything on that machine: the working tree, `.repomarshal/`, even the
RepoMarshal installation. The authoritative verdict is a re-run of `repomarshal done` somewhere the
agent cannot write. CI is the usual choice ([architecture §7](../architecture.md#7-tamper-resistance-and-trust-boundary),
[threat model](../security/threat-model.md) T4, T5, T7, T12, T20).

This repository runs the recipe below on itself: see the `dogfood` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

## 1. Pin BASE explicitly

Pass the pull request's base commit: `repomarshal done --base <base sha>`.

Policy (`repomarshal.toml`) and gate definitions are read from BASE, and the diff against BASE
decides which gates are required. Without `--base`, RepoMarshal resolves BASE automatically: the
merge-base with `origin/HEAD`, then `origin/main`/`origin/master`, then local `main`/`master`. That
is weaker in CI:

- a shallow clone may not contain the merge-base, or any of those refs;
- the result depends on which refs the checkout happened to fetch;
- whoever controls the refs controls which policy judges the change.

The PR base SHA comes from the CI platform's event data, not from the repository, so the change
under review cannot move it. Check out full history (`fetch-depth: 0` on GitHub) so that the base
commit and the diff are both available.

## 2. Install the tools so the gates are AVAILABLE

`done` runs the repository's own commands: inferred ones (package.json scripts, ruff, mypy, pyright,
pytest) and any `[gates.*]` in `repomarshal.toml` at BASE. If a required gate's command is not
installed, that gate is UNAVAILABLE and the verdict is **UNKNOWN**, never PASS. Install the same
toolchain a developer would use (`pip install -e ".[dev]"`, `npm ci`, ...) before running `done`.
`repomarshal done --dry-run` lists the gates that would run without running them.

**Order matters.** Install RepoMarshal first, in its own virtual environment, before any step that
runs the pull request's code. A project install (`pip install -e .` runs the PR's build backend,
`npm ci` runs install scripts) executes that code as the job's user, and code that runs before the
judge could plant `git replace` refs or git config that change what BASE looks like. `done` itself
reads the BASE policy, CODEOWNERS and waivers before the first gate runs. Where you can, install
tools without executing PR code (from BASE's requirements, `npm ci --ignore-scripts`), as this
repository's own dogfood job does.

## 3. Exit codes

| Exit | Verdict | Job result |
|---|---|---|
| 0 | PASS | success |
| 1 | FAIL | failure |
| 2 | UNKNOWN | failure |
| 3 | BLOCKED (invalid policy, not a git repository) | failure |
| 4 | internal error | failure |

Use the exit code as the job's result. Do not mask it with `continue-on-error`, `|| true` or similar.
UNKNOWN failing the job is deliberate: "could not check" is not "passed". Fix it by making the gate
available (install the tool, or define the gate in `repomarshal.toml` on the base branch), not by
ignoring it.

Make the job a **required status check** in branch protection (or a ruleset) for the protected
branch, so a PR cannot merge unless it passes. Require the job by its exact check name, e.g.
`ci / dogfood`, and keep that name stable: renaming the job silently drops the requirement.

## 4. Trust caveats

- **The workflow file comes from the PR.** A `pull_request` workflow runs the version of
  `.github/workflows/*.yml` in the PR's merge commit. A PR can edit the job to skip `done` or to
  always exit 0. The status check still reports, so the protection is human: require review for
  workflow changes with CODEOWNERS on `.github/workflows/` plus "Require review from Code Owners",
  and do not let the change's author approve it. RepoMarshal itself reads policy from BASE, but it
  cannot protect the workflow that invokes it.
- **Never use `pull_request_target` with a checkout of the PR's code.** That event runs with the
  base repository's token and secrets, so the PR's code (which the gates execute) would run with
  them.
- **The gates run the PR's code.** Tests, linters and scripts are the change's own code. Give the
  job no secrets and no write permissions: `permissions: contents: read`, `persist-credentials:
  false` on checkout, no deploy keys or cloud credentials in its environment.
- **A PASS is still only as good as the tests.** RepoMarshal proves which commands ran against which
  exact content and how they exited, not that the tests are meaningful.

## 5. Keep the evidence

`done` writes an evidence record under `.repomarshal/evidence/`, and `--json` prints the verdict,
reasons, and the record. Upload both as a build artifact so a reviewer can see what ran, against
which tree digest and BASE, without re-running it. Upload with `if: always()`: the evidence matters
most when the verdict is not PASS. Evidence is an audit trail; it is never an input to a verdict.

## 6. Waivers

When a required gate cannot run (UNAVAILABLE or UNKNOWN), a maintainer can accept the gap for one
pull request with a waiver. The full rules are in [waivers.md](waivers.md); in short:

- **Grant:** a `repomarshal-waive:<target>` label applied by a CODEOWNER or an admin/maintainer,
  plus a PR comment by the same person with a line `repomarshal-waive <target>: <reason>`, both
  made by a person, not through a GitHub App (`performed_via_github_app` must be null). Never a
  bot; never the PR author unless BASE sets `allow_author_waivers = true`. Reviews never grant a
  waiver: the reviews API cannot show they were not made through an app. CODEOWNERS is read at
  BASE.
- **Counts only if BASE permits:** `[completion] waivers_permit_pass = true` in `repomarshal.toml`
  on the base branch. Otherwise a waived gate stays UNKNOWN.
- **Never waivable:** a FAIL, a tree that changed during the run, opaque content, "nothing was
  verified".
- **Fail closed:** any GitHub API or network error means no waivers for that run.
- **Local waiver files are ignored in CI.** `repomarshal waive` is for a human at a terminal.

To enable it, run `done --waivers github` and give that job (only) a read-only token for pull
requests and issues, and re-run it when labels or reviews change:

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
  pull_request_review:
    types: [submitted, dismissed]
jobs:
  done:
    permissions:
      contents: read
      pull-requests: read   # reviews
      issues: read          # label events and PR comments
    steps:
      # ... checkout, install, as in the example below ...
      - name: repomarshal done
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          GITHUB_TOKEN: ${{ github.token }}
        run: repomarshal done --base "$BASE_SHA" --waivers github --json > repomarshal-done.json
```

Notes:

- The gates run the PR's code in the same job and can read `GITHUB_TOKEN`. That is why it must
  stay read-only: never add write scopes (or `pull_request_target`) to this job.
- Label events re-run every job of the workflow, not just this one. Do not skip the other jobs
  with `if:` on those events: a skipped job reports success, and it would stand in for a failed or
  cancelled run of the same check on the same commit. This repository's `ci.yml` accepts the extra
  runs for that reason.
- Post the reason comment **before** applying the label: a comment does not re-run the workflow.
- Pass the base SHA through `env`, as above, rather than interpolating `${{ }}` into the script.

## 7. GitHub Actions example

Pin actions by full commit SHA. The SHAs below are the ones this repository uses; re-resolve them
with `git ls-remote https://github.com/<owner>/<repo>.git 'refs/tags/<tag>*'` (for an annotated tag,
use the peeled `^{}` commit).

```yaml
name: repomarshal
on:
  pull_request:
permissions:
  contents: read
concurrency:
  group: repomarshal-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  done:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      # Install RepoMarshal (pinned to a commit) FIRST, in its own venv, then every tool your
      # gates run (see "Order matters" in section 2).
      - run: |
          python -m venv "$RUNNER_TEMP/rm-venv"
          "$RUNNER_TEMP/rm-venv/bin/python" -m pip install \
            "repomarshal @ git+https://github.com/MHHamdan/repo-marshal@<commit-sha>"
      - run: python -m pip install -e ".[dev]"   # your project's toolchain: ruff, pytest, ...
      - name: repomarshal done
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: |
          set +e
          "$RUNNER_TEMP/rm-venv/bin/repomarshal" done --base "$BASE_SHA" --json > repomarshal-done.json
          code=$?
          set -e
          cat repomarshal-done.json
          exit "$code"
      - if: always()
        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: repomarshal-evidence
          path: |
            repomarshal-done.json
            .repomarshal/evidence/
```

RepoMarshal is not yet on PyPI, so install it from a pinned commit (or a wheel you build). Passing
the SHA through `env` rather than interpolating `${{ }}` into the script is a habit worth keeping,
even for a SHA.

### Split verification (v0.4, recommended)

The single job above runs the PR's code in the same job, as the same user, as the judge. Code
that runs before or between gates can tamper with `.git`, the judge's files, or other gates'
results (threat T26). `repomarshal ci` splits the work so PR code never runs where the judge reads
or decides anything. This repository's own `ci.yml` dogfood jobs are the reference:

| Job | Runs PR code | Permissions | Does |
|---|---|---|---|
| `rm-plan` | no | contents, pull-requests, issues: read | `repomarshal ci plan --base SHA --github-output`: the plan, a check that the checkout is the PR head (or GitHub's merge commit of it), the waivers for the record, and a matrix of the gates that run repository code |
| `rm-gate-N` (matrix) | yes, one gate | **none** (`permissions: {}`) | `repomarshal ci gate --gate ID --expect ...`: runs that one gate on a fresh runner and uploads `result.json` as artifact `rm-gate-N`. Steps `run gate` (fails exactly on FAIL), `gate passed` (`repomarshal ci passed`, succeeds exactly on PASS) and `upload result` are the contract |
| `dogfood` | no | the above plus `actions: read` | `repomarshal ci verdict --expect ... --results DIR`: re-plans, re-reads the waivers, runs the native checks itself, binds each gate result and folds the verdict |

A gate result counts only when it binds:
- the verdict job's own plan hash, HEAD, tree digest and gate list equal the plan job's outputs;
- the checkout is the PR head SHA, or a merge commit whose second parent is the PR head SHA;
- the result file names the same gate, plan hash, HEAD and tree, and the gate did not change the
  tree;
- GitHub's record of the `rm-gate-N` job, read through the Actions API, decides the status:
  `run gate` failed means FAIL; `gate passed` succeeded means PASS; anything else is UNKNOWN. The
  file must agree, and `upload result` must have succeeded, for its details to be used.

A PASS needs all of these. A FAIL recorded by GitHub stays FAIL even without a usable file;
anything else is UNKNOWN. PR code can read the runner's artifact token, so a gate job could
upload a forged file under another gate's name. It cannot change another job's step
conclusions, so a forgery can never create a PASS, nor turn a real FAIL into a waivable UNKNOWN. A gate's report about itself remains only as good as the PR's code, as in any
CI.

Notes:
- Every job installs the same tools: tool availability is part of the plan, and the plans must
  match.
- Checkout `ref: ${{ github.sha }}` in every job, so all jobs see the same commit.
- On a private repository the gate jobs need `contents: read` to check out. That token is
  read-only and can only see code the PR author can already read.
- Re-running only failed jobs starts a new run attempt. The verdict job reads the jobs of its
  own attempt, so re-run all jobs.
- Until the BASE judge has `repomarshal ci` (v0.4), the workflow falls back to the single-job
  `done`.

### The trusted verdict and branch protection (v0.5)

A `pull_request` workflow runs the PR's own copy of the workflow file, so a PR can rewrite any
job in it, including a verdict job (threat T31). The verdict therefore comes from a second
workflow, `.github/workflows/repomarshal-verdict.yml`. It is triggered by `workflow_run` when
the `ci` workflow completes, and GitHub always runs `workflow_run` workflows from the default
branch.

How it works:

1. The PR's `ci` run has the `rm-plan` job and the permission-less `rm-gate-N` jobs. They check
   out the PR head SHA.
2. `repomarshal-verdict` runs from the default branch. It posts `repomarshal/verdict: pending`
   on the PR head, installs the judge from the PR's base commit (which must be on the base
   branch), and checks out the PR head as data only. It then runs `repomarshal ci
   trusted-verdict`, which:
   - checks with the Actions API that the triggering run is a `pull_request` run of
     `.github/workflows/ci.yml` for that head SHA and attempt;
   - finds the single open PR with that head;
   - refuses the gate records if the PR changed `.github/workflows/ci.yml` since its merge base;
   - re-plans, re-reads the waivers, runs the native checks, and binds each `rm-gate-N` result
     to GitHub's step record of that job (as in the split above);
   - posts `success` only for PASS, `failure` for FAIL, and `error` for everything else,
     including a judge that did not finish.
3. If a PR edits `ci.yml`, the verdict is UNKNOWN (`error`) unless a person reviews the change
   and grants the waiver target **`ci-workflow`**: the label `repomarshal-waive:ci-workflow`
   plus a comment line `repomarshal-waive ci-workflow: <reason>`. The usual waiver rules apply:
   made by a person, not through an app; BASE `waivers_permit_pass`; author only with
   `allow_author_waivers`. It accepts the workflow change, not any gate result, and is recorded
   in the evidence. If a PR renames or deletes the `ci` workflow, `repomarshal-verdict` never
   runs, and the required status stays "Expected", which blocks the merge. A missing or skipped
   `rm-gate-N` job is UNKNOWN.

#### Branch protection: require exactly one check

Require **`repomarshal/verdict`**, the commit status, and nothing else from RepoMarshal. Do not
require `dogfood (advisory)` or any `rm-*` job: those come from the PR-controlled workflow. Also
turn on "Do not allow bypassing the above settings" (or include administrators), and block force
pushes to the default branch.

#### Who may post that status: a GitHub App (strongly recommended)

The workflow token cannot prove where a status came from. Any workflow run in the repository can
request `statuses: write` and post a status named `repomarshal/verdict` on any commit. That
includes a `push` workflow added on some unrelated branch by whoever (or whatever agent) can push
branches. Statuses posted with the workflow token are all attributed to the same
`github-actions` identity. So, without the next steps, the required check can be forged by
anyone with push access.

To make it unforgeable:

1. Create a GitHub App (Settings, then Developer settings, then GitHub Apps, then New). Turn the
   webhook off. Repository permissions: **Commit statuses: Read and write** only. Install it on
   this repository.
2. Create the environment **`repomarshal-verdict`** (Settings, then Environments). Under
   "Deployment branches and tags" choose "Selected branches" and allow only the default branch.
3. Add two environment secrets: `REPOMARSHAL_APP_CLIENT_ID` (the App's client ID) and
   `REPOMARSHAL_APP_PRIVATE_KEY` (a private key generated for the App).
4. In branch protection, require `repomarshal/verdict` and set its expected source to **that
   App**.

The trusted workflow then posts the status with an App token. Only jobs on the default branch can
reach the environment, and no workflow a PR branch can run is on the default branch. The `ci`
workflow, its `rm-gate` jobs and any workflow a PR adds cannot get the secret, so a status they
post does not satisfy the requirement. Keep the App's key out of agent environments.

## 8. Other CI systems

The recipe is the same everywhere: full history, an explicit BASE from the platform (not from the
repository), tools installed, the exit code respected, evidence kept, no secrets.

```sh
#!/bin/sh
# BASE_SHA: the target branch commit your CI platform reports for this merge request.
set -u
git cat-file -e "$BASE_SHA^{commit}" || git fetch --no-tags origin "$BASE_SHA"  # BASE must exist
repomarshal done --base "$BASE_SHA" --json > repomarshal-done.json
code=$?
cat repomarshal-done.json
exit "$code"                                # 0 PASS; 1 FAIL, 2 UNKNOWN, 3 BLOCKED, 4 error
```
