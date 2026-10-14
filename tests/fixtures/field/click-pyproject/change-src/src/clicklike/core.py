"""Synthetic module."""


def main(args: list[str]) -> int:
    try:
        return len(args)
    except KeyboardInterrupt:
        return 1
