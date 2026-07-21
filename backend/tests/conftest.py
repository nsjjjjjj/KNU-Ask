import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MOCK_AI"] = "true"
os.environ["CHAT_PROVIDER"] = "rules"
os.environ["NOTICE_STRUCTURING_PROVIDER"] = "rules"
os.environ["CODEX_ENRICHMENT_ENABLED"] = "false"
os.environ["MOCK_CRAWLER"] = "true"
os.environ["EMBEDDING_PROVIDER"] = "lexical"
os.environ["CRAWLER_SCHEDULE_ENABLED"] = "false"
os.environ["ADMIN_API_TOKEN"] = "test-only-admin-token-32-characters"

import pytest
from fastapi.testclient import TestClient

import app.models  # noqa: F401,E402
from app.db.seed import seed  # noqa: E402
from app.db.session import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
