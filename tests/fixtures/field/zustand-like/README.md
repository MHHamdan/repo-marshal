# zustand-like

Synthetic equivalent of [pmndrs/zustand](https://github.com/pmndrs/zustand) (MIT) at
`d7a5583cffd80af515f7dfb69583c95cbdc9e2ce`: a single TypeScript package whose `test` script runs
every `test:*` script (format, types, lint, spec), exactly as upstream `package.json` and
`.github/workflows/test.yml` do. Library code is `.ts`; tests are `.test.tsx` because they render
React hooks. The lockfile is a synthetic stub; sources and tests are synthetic.

| Variant | Mirrors upstream commit | Change |
|---|---|---|
| `change-src/` | `3febf8c` "fix(persist): clearStorage() should invalidate concurrent async rehydration (#3555)" | edits `src/middleware/persist.ts` and `tests/persistAsync.test.tsx` |
