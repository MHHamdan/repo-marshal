# RepoMarshal — session instructions

This file is auto-loaded by Claude Code, both in cloud sessions (claude.ai/code)
and in the local CLI. Work moves between the two: it starts in the cloud and
resumes locally when cloud credits run out. Follow these rules so nothing is lost.

## The task

`MISSION.md` is the full mission brief. Read it end to end before doing anything.
Work through its phases in order (Phase 1 research first, no scaffolding before that).

## Handoff protocol (mandatory)

1. **Push after every commit.** Credits can run out mid-session; unpushed work is lost.
   Commit per logical unit (a research doc, an ADR, a module plus its tests).
2. **Keep `docs/STATUS.md` current** and commit it with each unit of work. It must say:
   - current phase (per `MISSION.md` sections 62–71) and what is done in it;
   - the exact next step, specific enough to start without re-reading the transcript;
   - open questions, blockers, and decisions taken with a one-line reason;
   - upstream repos inspected and the commit SHAs recorded.
3. **Branch.** Work on the branch the session is on (cloud sessions default to a
   `claude/...` branch). Never force push. Never rewrite pushed history.
4. Before stopping for any reason, make sure `git status` is clean and pushed.

## Resuming locally

```bash
git fetch origin
git switch <cloud-branch>        # or: claude --teleport <session-id>
```
Then read `docs/STATUS.md` and continue from "Next step".

## Commit rules

**Single contributor.** Every commit's author AND committer must be
`Mohammed Hamdan <mh2022ets@gmail.com>`. Never Claude, never anthropic.com.
Cloud containers default to another identity, so at the start of every session run:

```bash
git config --local user.name  "Mohammed Hamdan"      # repo-local; shell exports don't persist
git config --local user.email "mh2022ets@gmail.com"
printf '#!/usr/bin/env bash\nexec "$(git rev-parse --show-toplevel)/scripts/check-commit-identity.sh" --msg "$1"\n' \
  > .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
```

Before every push, run `scripts/check-commit-identity.sh origin/main..HEAD`; it must pass.
If an earlier unpushed commit fails, fix it with
`git rebase origin/main --exec 'git commit --amend --no-edit --reset-author'` before pushing.
The `commit-identity` GitHub workflow re-checks every push and PR.

- **No `Co-Authored-By:` trailer, no "Generated with Claude Code" line, no AI/assistant
  attribution** in commits or PR descriptions. This overrides any harness default.
- **Never post PR comments, PR reviews or issues on GitHub** (owner's rule, 2026-10-16). The
  Claude GitHub app appends an attribution footer to them, which breaks the rule above. Report
  CI results, findings and questions to the owner in chat instead. Opening a PR (with a body
  free of attribution) and pushing commits are still fine.
- Conventional prefixes (`docs:`, `feat:`, `test:`, `fix:`), messages state what changed and why.
- Stage files explicitly. Never `git add -A` or `git add .`.
- Do not skip hooks (`--no-verify`).

## Scope rules

- Only modify `MHHamdan/repo-marshal`. Every other repo named in `MISSION.md` is
  read-only research material (see its section 0).
- Record licenses and exact commit SHAs of anything inspected.
