"""Starts the real LATCHA site on a spare port with a throw-away database.

Nothing here touches your Neon database or your live site.
"""
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.security import hash_password  # noqa: E402

ADMIN_USER = "owner"
ADMIN_PASSWORD = "e2e-test-password-123"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "e2e.db"
    log = tmp_path_factory.mktemp("log") / "server.log"
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db.as_posix()}",
        "JWT_SECRET": "e2e-secret-" + "x" * 30,
        "ADMIN_USERNAME": ADMIN_USER,
        "ADMIN_PASSWORD_HASH": hash_password(ADMIN_PASSWORD),
        "CORS_ORIGINS": f"http://127.0.0.1:{port}",
    }
    subprocess.run([sys.executable, "scripts/init_db.py"], cwd=ROOT, env=env, check=True)
    with open(log, "w") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(60):
                try:
                    if urllib.request.urlopen(url + "/api/health", timeout=1).status == 200:
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                pytest.fail("Server did not start. Log:\n" + log.read_text())
            yield url
        finally:
            proc.terminate()
            proc.wait(timeout=10)


@pytest.fixture(scope="session")
def base_url(live_server):
    return live_server


@pytest.fixture(scope="session")
def admin_password():
    return ADMIN_PASSWORD
