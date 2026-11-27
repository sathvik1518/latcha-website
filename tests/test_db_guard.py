"""No test may ever run against the live database named in .env."""


def test_tests_never_use_the_live_database():
    from app.db import engine
    assert engine.url.get_backend_name() == "sqlite" or engine.url.render_as_string() == __import__("os").environ.get("TEST_DATABASE_URL")
