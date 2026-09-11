import pytest
from fastapi.testclient import TestClient

from mailer.app import create_app
from mailer.config import Settings
from mailer.sender import DryRunSender
from mailer.store import Store

KEY = "test-secret-key"


@pytest.fixture
def api_key() -> str:
    return KEY


@pytest.fixture
def settings() -> Settings:
    return Settings(
        smtp_host="",
        smtp_port=465,
        smtp_user="",
        smtp_password="",
        smtp_security="ssl",
        default_from="Test <test@latentnoise.dev>",
        api_keys={KEY: "content", "other-key": "studio"},
        db_path=":memory:",
        max_attempts=3,
        retry_base_seconds=10,
        dry_run=True,
    )


@pytest.fixture
def store() -> Store:
    store = Store(":memory:")
    yield store
    store.close()


@pytest.fixture
def sender() -> DryRunSender:
    return DryRunSender()


@pytest.fixture
def client(settings, store, sender) -> TestClient:
    app = create_app(settings, store=store, sender=sender, start_worker=False)
    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {KEY}"
        yield test_client
