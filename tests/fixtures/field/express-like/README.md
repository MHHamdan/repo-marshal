# express-like

Synthetic equivalent of [expressjs/express](https://github.com/expressjs/express) (MIT) at
`a419aa7350389a275ab45fde2e849ed1f02449ef`: npm, no lockfile (`package-lock=false`), `lint`
and `test` scripts copied from upstream `package.json`, a shortened dependency list. Sources and
tests are synthetic.

| Variant | Mirrors upstream commit | Change |
|---|---|---|
| `change-deps/` | `3b7e39f` "deps: bump qs minimum to 6.16.0 (#7478)" | `dependencies.qs` `^6.15.2` -> `^6.16.0` |
| `change-src/` | `9a34acf` "fix(res.send): preserve ETag generation with Transfer-Encoding (#7459)" | edits `lib/response.js` and `test/res.send.js` |
| `change-npmrc/` | `2eae22b` "chore: ensure safe config in the npmrc (#7144)" | adds three settings to `.npmrc` (upstream lines) |
