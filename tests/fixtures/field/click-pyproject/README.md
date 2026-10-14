# click-pyproject

Synthetic equivalent of the tool configuration of
[pallets/click](https://github.com/pallets/click) (BSD-3-Clause) at
`2247b35ea1c47c727d7a06e51fa280e12a863ff6`. The `[tool.mypy]`, `[tool.pyright]` and
`[tool.ruff]` tables keep only the keys that matter here (`files`, `include`, `fix`); every
source and test file is synthetic.

| Variant | Mirrors upstream commit | Change |
|---|---|---|
| `change-src/` | `fc518e4` "Fix race condition on `KeyboardInterrupt` ..." | edits `src/clicklike/core.py` |
| `change-docs/` | `fa99fbd` "Use the `kbd` role for keyboard sequences in the docs" | edits `docs/utils.md` |
| `change-tests-split/` (+ `change-tests-split.deleted`) | `b8a4486` "Split out types tests." | moves `tests/test_types.py` into `tests/test_types/` |
| `change-ci/` | `0c9e836` "Update all GitHub actions, fix thread locking" | bumps action pins in `.github/workflows/tests.yaml` |

`[tool.mypy] files = [...]` is also set by Textualize/rich (`files = ["rich"]`).
