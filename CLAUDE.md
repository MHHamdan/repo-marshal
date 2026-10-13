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

- Conventional prefixes (`docs:`, `feat:`, `test:`, `fix:`), messages state what changed and why.
- **No `Co-Authored-By:` trailer and no AI/assistant attribution** in commits or PRs.
- Stage files explicitly. Never `git add -A` or `git add .`.
- Do not skip hooks (`--no-verify`).

## Scope rules

- Only modify `MHHamdan/repo-marshal`. Every other repo named in `MISSION.md` is
  read-only research material (see its section 0).
- Record licenses and exact commit SHAs of anything inspected.
