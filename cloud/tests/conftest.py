import sys
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import Queue

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parent)]          # jhcloud + the desktop jobhunter package

from jhcloud import tasks  # noqa: E402
from jhcloud.db import init_db, make_engine, make_sessionmaker  # noqa: E402
from jhcloud.settings import ServerSettings  # noqa: E402
from jhcloud.web import create_app  # noqa: E402

SECRET = "test-secret-" + "x" * 40


@pytest.fixture
def server(tmp_path):
    settings = ServerSettings(database_url=f"sqlite:///{tmp_path / 'cloud.db'}", redis_url="redis://unused",
                              secret_key=SECRET, storage_dir=tmp_path / "storage", ingest_every_minutes=30,
                              secure_cookies=False)
    engine = make_engine(settings.database_url)
    init_db(engine)
    Session = make_sessionmaker(engine)
    redis = fakeredis.FakeRedis()
    tasks.configure(settings, Session)
    tasks.set_queue(Queue("jobhunter", connection=redis, is_async=False))   # tasks run at once in tests
    app = create_app(settings, redis=redis, sessionmaker=Session)
    return {"settings": settings, "Session": Session, "app": app, "redis": redis}


@pytest.fixture
def client(server):
    return TestClient(server["app"])


def signup(client, email="nan@example.com", password="a-long-password"):
    return client.post("/signup", data={"email": email, "password": password}, follow_redirects=False)
