"""Shared test set-up. pytest loads this file before any test file, so it runs first."""
import os
import pathlib
import tempfile

import pytest

# Safety barrier: point the app at a throw-away database BEFORE anything imports it, so no test can ever reach
# the DATABASE_URL in .env (the live Neon database). The test files set their own temporary database too.
os.environ["MAX_ORDERS_PER_IP_HOUR"] = "100000"   # every test order comes from one machine; the limit has its own test
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'guard.db'}"


@pytest.fixture(autouse=True)
def shops_open(monkeypatch):
    """Every order goes to an outlet, so tests must not depend on the real clock: today's date, 3 PM in India,
    when every open outlet takes orders. A test that needs another hour sets its own."""
    from app import outlets as ot   # imported here, never at the top: see the barrier above
    monkeypatch.setattr(ot, "now_ist", lambda: ot.datetime.now(ot.IST).replace(hour=15, minute=0))
