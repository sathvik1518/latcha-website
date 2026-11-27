"""No test may ever run against the live database named in .env."""


def test_tests_never_use_the_live_database():
    from app.db import engine
    # a throw-away SQLite file, or a Postgres running on this machine / in CI (never a hosted one like Neon)
    assert engine.url.get_backend_name() == "sqlite" or engine.url.host in ("localhost", "127.0.0.1")
