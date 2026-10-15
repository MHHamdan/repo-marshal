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
# v0.8: an SSH allowed-signers file at BASE. Every label waiver then needs a signed code, and an
# author waiver is accepted ONLY with one (see "Signed waiver codes" below).
waiver_signers = ".github/repomarshal_allowed_signers"
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
- the label is on the PR right now (label names compare without regard to case);
- the same account wrote a PR comment with a `repomarshal-waive <target>: <reason>` line and a
  non-empty reason. No reason, no waiver. The line must be prose: inside a code block, an
  indented block, a block quote or an HTML comment it does not count. The comment must not
  have been edited after it was posted (an edit can come from an app acting as you); post a
  new comment instead;
- when the BASE policy sets `waiver_signers` (v0.8), that same comment also carries a valid
  signed code for the PR's current head (next section). An author waiver needs this in every
  case.

**Reviews do not grant waivers (v0.4).** Earlier versions also accepted an approving CODEOWNER
review with a `repomarshal-waive <target>: <reason>` line. No GitHub API says whether a review was
submitted through a GitHub App (the REST reviews and timeline payloads have no
`performed_via_github_app` field, and GraphQL `PullRequestReview` has only `createdViaEmail`), so
a review from an agent is indistinguishable from the maintainer's. Review waivers are therefore
always rejected, with a note pointing to the label route. If GitHub adds the field, the same check
will accept a review made by a person.

**`ci-workflow` (v0.5, trusted CI verdict only).** A PR that changes `.github/workflows/ci.yml`
can redefine the gate jobs, so the trusted verdict does not trust their records. After reviewing
the workflow diff yourself, grant the target `ci-workflow` with the same label and comment, and
the records bind again. Every gate still needs its own result or waiver.

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

## Signed waiver codes (v0.8, threat T40)

**Why.** A coding agent on your machine that can use your `gh` login or a personal access token
makes labels and comments that GitHub records as yours, with `performed_via_github_app: null`,
exactly like your clicks in the web UI. Nothing in the GitHub API tells the two apart. So since
v0.8 an author waiver must also prove that a person with your signing key approved it. The
alternatives that were weighed are in the threat model, T40.

**It is only as strong as the key's protection.** Use a FIDO hardware key that needs a touch
for every signature (`ed25519-sk`). An agent can ask the key to sign, but only your finger
completes it. **Touch the key only when you started the signature yourself.** A software key
that is loaded in `ssh-agent`, or has no passphrase, can be used by any process running as you,
the agent included, and then protects nothing.

### One-time setup

1. Create a hardware-backed signing key (OpenSSH 8.2 or later, and a FIDO2 key):

   ```sh
   ssh-keygen -t ed25519-sk -O verify-required -C "repomarshal waivers" -f ~/.ssh/id_repomarshal_sk
   ```

   `-O verify-required` also asks for the key's PIN. Leave it out if your key has no PIN.
2. Add your public key to the allowed-signers file, with your **GitHub login** as the principal,
   spelled exactly as on your GitHub profile (the match is case-sensitive), and commit it to the
   default branch. `namespaces=` limits this key to waiver codes:

   ```text
   # .github/repomarshal_allowed_signers
   MHHamdan namespaces="repomarshal-waiver",verify-required sk-ssh-ed25519@openssh.com AAAA... repomarshal waivers
   ```

   Drop `verify-required` from the line if you dropped it in step 1. Never add
   `no-touch-required`.
3. Name the file in `repomarshal.toml` (`waiver_signers`, above). Both files are read from BASE,
   so they take effect after they merge. Review every later change to the signers file yourself:
   whoever can add a key there can grant waivers (T42).

### Granting a waiver

For each target, on the PR's **current** head:

```sh
repomarshal waive-sign root:unit \
  --reason "discovery taint comes only from test fixture pyproject files" \
  --repository MHHamdan/repo-marshal --pr 7 \
  --head "$(gh pr view 7 --json headRefOid -q .headRefOid)" \
  --key ~/.ssh/id_repomarshal_sk
```

It asks for a touch (and PIN), then prints two lines:

```text
repomarshal-waive root:unit: discovery taint comes only from test fixture pyproject files
repomarshal-waive-sig root:unit 2026-10-15T09:30:00Z U1NIU0lHAAAAAQAAAEoAAAAac2stc3NoLWVk...
```

Paste them into a **new** PR comment as plain text (not in a code block), with no edits,
**within 30 minutes** of signing. Codes for several targets can share one comment. Then apply
the label `repomarshal-waive:root:unit`. Post the comment first: adding the label is what
re-runs CI.

Without RepoMarshal installed, the same code is:

```sh
SIGNED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf 'repomarshal-waiver/1\nrepository: %s\npr: %s\nhead: %s\ntarget: %s\nreason: %s\nsigned-at: %s\n' \
  MHHamdan/repo-marshal 7 "$HEAD_SHA" root:unit "discovery taint comes only from test fixture pyproject files" "$SIGNED_AT" \
  | ssh-keygen -Y sign -f ~/.ssh/id_repomarshal_sk -n repomarshal-waiver | sed '1d;$d' | tr -d '\n'
```

The comment line is then `repomarshal-waive-sig root:unit $SIGNED_AT <that output>`. The
reason must match the comment's reason exactly.

**What the code binds.** It covers the repository, PR number, head SHA, target, reason and the
signing time (`signed-at`). It is checked with `ssh-keygen -Y verify` against the
allowed-signers file at BASE, with the comment author's login as the principal. A new push makes
it invalid, so sign again for the new head. A code for another PR, target or reason, or one made
with any other key, is rejected with a note in the CI log.

**Withdrawing a waiver.** Remove the label. A code signed before the label's most recent removal
never counts again, and a comment must be posted within 30 minutes of its `signed-at` (5 minutes
of clock skew allowed). So an agent can neither re-add the label to revive your old comment nor
repost a copy of it. To grant the waiver again, sign a fresh code.

**Fail closed.** If the signers file is missing at BASE, the head SHA is unknown, or `ssh-keygen`
is missing on the runner (GitHub's Ubuntu runners have it), no signed waiver is accepted.

### What the agent can and cannot do

The coding agent usually works as the PR author. Unless the policy allows author waivers, that
excludes it outright. Either way it cannot apply a label as somebody else, write a comment as
somebody else, or change CODEOWNERS or the policy at BASE. When it acts through a GitHub App, even
under the maintainer's own account, its labels and comments carry `performed_via_github_app` and
are rejected. An agent holding a maintainer's own credentials outside an app (a personal access
token, a logged-in `gh` or browser session) is, to GitHub, that maintainer (T40). With
`waiver_signers` set, it still cannot produce the signed code unless it can use the signing key,
which a touch-to-sign hardware key prevents.

Residual risks to know about:

- Anyone with write access can edit another user's comment, so a writer could change the reason
  text of a human's label waiver. The label (and so the target) still has to come from an
  authorised human.
- An unsigned label waiver is not bound to a commit: it stays valid after later pushes until
  someone removes the label. Remove `repomarshal-waive:*` labels once they are no longer meant to
  apply. A signed waiver is bound to the head SHA.
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
