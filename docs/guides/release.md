# Releasing RepoMarshal to PyPI

Releases are published by `.github/workflows/release.yml` using PyPI **trusted publishing**:
GitHub's OIDC token proves to PyPI which repository, workflow and environment is uploading, so
no API token exists anywhere: not in GitHub secrets, not on a laptop.

## One-time setup

### On PyPI (the project does not exist yet, so add a *pending* publisher)

1. Sign in at <https://pypi.org> with the account that should own the project, and enable 2FA
   (PyPI requires it).
2. Open **Your account → Publishing** (<https://pypi.org/manage/account/publishing/>).
3. Under **Add a new pending publisher**, choose **GitHub** and enter exactly:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `repomarshal` |
   | Owner | `MHHamdan` |
   | Repository name | `repo-marshal` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

4. Click **Add**. The first successful upload creates the project and turns the pending publisher
   into a normal one (visible afterwards under the project's **Settings → Publishing**).

The name `repomarshal` was still unregistered on PyPI on 2026-10-14 (`/pypi/repomarshal/json` returned 404). A pending publisher does not
reserve it, so publish soon after adding it.

### On GitHub (repository settings)

1. **Environment `pypi`** (Settings → Environments → New environment → `pypi`):
   - **Required reviewers**: add yourself. Every upload then waits for your click, after the
     build has passed.
   - **Deployment branches and tags**: "Selected branches and tags", add the **tag** rule `v*`.
   - No secrets: trusted publishing needs none.
2. **Signed tags**: add the GPG or SSH key you sign with as a **Signing key** under your GitHub
   account (Settings → SSH and GPG keys), so that GitHub reports the tag's signature as
   verified. The workflow refuses tags whose `verification.verified` is not `true`.
3. **Repository variable `RELEASE_TAGGER_EMAILS`** (Settings → Secrets and variables → Actions →
   Variables): the comma-separated tagger emails allowed to release, e.g. your own. GitHub
   verifies a tag only when the signing key belongs to an account with that verified email, so
   this pins *who* released. Without the variable, every release is refused.
4. Optional but recommended: a **tag ruleset** for `v*` that restricts tag creation to you and
   blocks updates and deletions, so a published version's tag cannot be moved.

## Cutting a release

```bash
git switch main && git pull
# pyproject.toml `version` and src/repomarshal/__init__.py `__version__` must both say 0.1.0
git tag -s v0.1.0 -m "RepoMarshal 0.1.0"
git tag -v v0.1.0          # check the signature locally
git push origin v0.1.0
```

Then approve the `publish` job in the Actions tab.

## What the workflow checks

The **build** job (`contents: read` only) refuses to build unless:

- the tag name is `v<major>.<minor>.<patch>`;
- the tag is an annotated tag whose signature GitHub reports as verified, by a tagger listed in
  `RELEASE_TAGGER_EMAILS`;
- the tag points at exactly the commit the run checked out;
- that commit is on the default branch (checked against `refs/remotes/origin/<default>`, so a
  tag named like the branch cannot stand in for it);
- the tag version, `pyproject.toml` and `repomarshal.__version__` agree.

It then builds the sdist and wheel from that commit, with pinned `build==1.2.2.post1` and
`hatchling==1.27.0`, smoke-tests the wheel, and uploads them as an artifact.

The **publish** job holds the only credential: `id-token: write` in the `pypi` environment. It
checks out nothing and runs no repository code. It downloads the files, checks that they are
exactly this version's sdist and wheel (build hooks ran repository code in the build job), and uploads them
with `pypa/gh-action-pypi-publish` (pinned by commit), which also publishes PEP 740
attestations.
