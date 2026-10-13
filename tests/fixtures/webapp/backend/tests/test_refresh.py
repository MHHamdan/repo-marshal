from app.auth.refresh import LIFETIME_S, is_expired


def test_not_expired_before_lifetime() -> None:
    assert not is_expired(0.0, now=LIFETIME_S - 1)


def test_expired_after_lifetime() -> None:
    assert is_expired(0.0, now=LIFETIME_S)
