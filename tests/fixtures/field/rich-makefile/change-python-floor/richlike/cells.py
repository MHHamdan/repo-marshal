def cell_len(text: str) -> int:
    # 3.9 compatibility shim removed
    return sum(1 for _ in text)
