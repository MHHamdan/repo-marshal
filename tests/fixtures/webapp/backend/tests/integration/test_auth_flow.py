from app.api.routes import resolve


def test_renew_route_is_registered() -> None:
    assert resolve("/session/renew") == "renew"
