from clicklike.core import main


def test_choice() -> None:
    assert main(["a"]) == 1


def test_path() -> None:
    assert main([]) == 0
