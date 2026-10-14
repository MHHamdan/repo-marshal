# zod-like

Synthetic equivalent of the monorepo layout of [colinhacks/zod](https://github.com/colinhacks/zod)
(MIT) at `680d19f7d1e042e55934209caeb79bc843b94f44`: `"workspaces": ["packages/*"]`,
`"packageManager": "nub@0.8.3"` next to a `pnpm-lock.yaml`, root scripts in which `lint` and
`format` rewrite files (`--write`) while `lint:check` and `format:check` only check (CI runs the
`:check` variants), a library package with its own `test` script and a Next.js docs package. Script
bodies are shortened (upstream prefixes them with `nub exec --node`); sources, tests and the
lockfile are synthetic.

| Variant | Mirrors upstream commit | Change |
|---|---|---|
| `change-src/` | `680d19f` "fix(v4): report a union option that only checks rejected (#6662)" | edits `packages/zod/src/v4/core/schemas.ts` |
| `change-test/` | `07917f4` "test(v4): pin the lazy safeParse error's stack behavior (#6548)" | edits `packages/zod/src/v4/classic/tests/error.test.ts` |
| `change-site/` | `e54716c` "docs(ecosystem): add @apical-ts/craft (#5946)" | edits `packages/docs/components/ecosystem.tsx` |
| `change-ci-script/` | `9c21983` "ci: stage at verbose so a failed OIDC token exchange says why" | edits `.github/scripts/npm-stage-publish.sh` |
| `change-lockfile/` | `59bbc03` "chore: re-pin the integration peers to the workspace zod after the 4.6.5 bump" | edits `pnpm-lock.yaml` only |

`change-lockfile/` also stands in for the lockfile part of `f6e1701` "chore(deps): bump next to
15.5.25 and vite to 7.3.6 (#6153)" (dependency signal and context size).
