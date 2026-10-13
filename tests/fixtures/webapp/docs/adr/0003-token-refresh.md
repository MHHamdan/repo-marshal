# ADR 0003: Token refresh

Status: accepted.

Access tokens are short-lived. The backend renews them with a refresh token, implemented in
`backend/app/auth/refresh.py` and covered by `backend/tests/test_refresh.py`.
