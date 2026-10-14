from richlike.cells import cell_len


def test_cell_len() -> None:
    assert cell_len("abc") == 3
