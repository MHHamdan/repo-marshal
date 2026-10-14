# rich-makefile

Synthetic equivalent of the test and tool layout of
[Textualize/rich](https://github.com/Textualize/rich) (MIT) at
`9d8f9a372cc5916fd4781fec207ced7ddac2f08f`: a Poetry `pyproject.toml` with `[tool.mypy]` and
`[tool.pytest.ini_options]`, a `Makefile` whose `format-check`, `typecheck` and `test` targets are
what CI runs (`.github/workflows/pythonpackage.yml`), and a second `pytest.ini` inside `tests/`.
Target bodies are shortened; sources and tests are synthetic.

| Variant | Mirrors upstream commit | Change |
|---|---|---|
| `change-src/` | `7ef2d05` "fix inline code in table cells" | edits `richlike/cells.py` |
| `change-test/` | `905b397` "Update tests/test_cells.py" | edits `tests/test_cells.py` |
| `change-python-floor/` | `285d9e9` "drop 3.8" | raises Poetry's interpreter floor (`python` in `[tool.poetry.dependencies]`) and edits `richlike/cells.py` |
| `change-dependency/` | (control) | bumps `pygments` in `[tool.poetry.dependencies]` |
