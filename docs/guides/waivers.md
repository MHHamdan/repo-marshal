# Waivers: accepting a gate that could not be checked

Sometimes a required gate cannot run: no security scanner is installed, the device lab is down,
there is no runtime smoke test yet. RepoMarshal reports that as **UNKNOWN**, never PASS. A waiver
is a person's recorded decision to accept that gap for one change (MISSION.md §54). It is
explicit, carries a reason, is listed in the evidence record, and never turns a failure into a
pass.

The design goal: **a waiver can come from a maintainer, never from the coding agent.**

## What can and cannot be waived

A waiver targets one gate id (`backend:unit`) or, for a decision with no gate, a kind
(`security_scan`, `runtime`). `repomarshal plan --explain` shows the targets. It only applies when
that gate's outcome is UNKNOWN or UNAVAILABLE.

Never waivable:

- a **FAIL**: a failing gate is negative evidence; it stays FAIL whatever is waived;
- the working tree changing during verification;
- content RepoMarshal could not inspect (opaque paths);
- "nothing was verified" (a plan with no required gates);
- an invalid policy (BLOCKED).

## The policy switch lives at BASE

Waivers count towards PASS only when the BASE commit's `repomarshal.toml` says so:

```toml
[completion]
waivers_permit_pass = true
# v0.4, optional: let the PR author grant a waiver. Needed when one maintainer opens every PR
# (GitHub does not let an author approve their own PR, and nobody else can grant one).
allow_author_waivers = true
```

Without `waivers_permit_pass`, waived gates are still listed (in the reasons and the evidence record) but the verdict
stays UNKNOWN. Because the policy is read from BASE, a pull request cannot turn the switch on for
itself; the change takes effect after it merges.

## Who can grant a waiver, and how (CI: `--waivers github`)

In GitHub Actions on a pull request, `repomarshal done --waivers github` reads the PR through the
GitHub API with the workflow token. The way to grant a waiver is a label plus a comment, made by a
person in the GitHub web UI (or another client that does not act through a GitHub App).

**1. A label plus a comment.** Apply the label `repomarshal-waive:<target>` to the PR, for example
`repomarshal-waive:security_scan`, and post a PR comment containing the line:

```text
repomarshal-waive security_scan: scanner licence expired, reviewed auth change by hand
```

The waiver is genuine when:

- the label is on the PR now, and the account that most recently applied it is a human (account
  type `User`, not a bot) and is not the PR author, unless the BASE policy sets
  `allow_author_waivers = true`;
- **neither the `labeled` event nor the reason comment was made through a GitHub App** (v0.4).
  GitHub records `performed_via_github_app` on both. A coding agent that acts on GitHub through
  an app under the maintainer's own account (for example the Claude GitHub app) has the
  maintainer's login, so the login alone proves nothing. An app value, or a missing or unreadable
  field, rejects the waiver;
- that account is a CODEOWNER for the target, or has `admin` or `maintain` permission on the
  repository (a failed permission lookup counts as no permission);
- the same account wrote a PR comment with a `repomarshal-waive <target>: <reason>` line and a
  non-empty reason. No reason, no waiver.

**Reviews do not grant waivers (v0.4).** Earlier versions also accepted an approving CODEOWNER
review with a `repomarshal-waive <target>: <reason>` line. No GitHub API says whether a review was
submitted through a GitHub App (the REST reviews and timeline payloads have no
`performed_via_github_app` field, and GraphQL `PullRequestReview` has only `createdViaEmail`), so
a review from an agent is indistinguishable from the maintainer's. Review waivers are therefore
always rejected, with a note pointing to the label route. If GitHub adds the field, the same check
will accept a review made by a person.

**CODEOWNERS** is read from the BASE commit (`.github/CODEOWNERS`, then `CODEOWNERS`, then
`docs/CODEOWNERS`), never from the PR. A user is a CODEOWNER for a target when they are on the `*`
rule, or when they own every changed path the target covers (last matching rule per path, as on
GitHub). Only individual `@user` owners count: `@org/team` entries and e-mail addresses are not
resolved.

**Fail closed.** Any API or network error (HTTP 500, a timeout, a redirect, malformed data) means
no waivers at all for that run; the reason is printed on stderr. Re-run the job once GitHub is
healthy.

The CI workflow re-runs on `labeled`, `unlabeled` and review `submitted`/`dismissed` events, so
adding or removing a waiver updates the status check.

### What the agent can and cannot do

The coding agent usually works as the PR author. Unless the policy allows author waivers, that
excludes it outright. Either way it cannot apply a label as somebody else, write a comment as
somebody else, or change CODEOWNERS or the policy at BASE. When it acts through a GitHub App, even
under the maintainer's own account, its labels and comments carry `performed_via_github_app` and
are rejected. This depends on the agent **not** holding a maintainer's own credentials outside
an app (a personal access token, a logged-in `gh` or browser session): whoever holds those is, to
GitHub, that maintainer, and nothing in the API can tell them apart.

Residual risks to know about:

- Anyone with write access can edit another user's comment, so a writer could change the reason
  text of a human's label waiver. The label (and so the target) still has to come from an
  authorised human.
- A label waiver is not bound to a commit: it stays valid after later pushes until someone removes
  the label. Remove `repomarshal-waive:*` labels once they are no longer meant to apply.
- The CI job runs the PR's own code, including, in this repository, RepoMarshal itself. Protect
  `.github/workflows/` and `src/` with CODEOWNERS and required code-owner review (see
  [ci.md §4](ci.md#4-trust-caveats)).

## Local waivers (`--waivers local`, the CLI default)

At your own terminal:

```sh
repomarshal waive runtime --reason "no device lab on this laptop"
```

`waive` refuses unless stdin and stdout are both terminals, shows what it will record, and asks
you to retype the gate id. It records `git config user.email` plus your OS user name in
`.repomarshal/waivers/<time>-<hash>.json`, bound to the current tree digest and target. `done`
ignores the file once the tree changes, when the target is not waivable in the current plan, or
when the file is malformed.

**Be clear about what this is.** A local waiver is a convenience for a human at the terminal, not
a security control. An agent running as you can drive a pseudo-terminal and pass the TTY check,
or simply write the JSON file. So local waivers are never used where it matters:

- `done --waivers local` returns no waivers when `GITHUB_ACTIONS=true` or `CI=true`;
- `--waivers github` ignores local waiver files;
- the agent Stop hook and the MCP server always use `--waivers none`.

`repomarshal done --waivers none` ignores every waiver.
