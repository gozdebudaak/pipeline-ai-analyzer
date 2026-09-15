from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An HTTP client wired directly to a fresh application instance.

    Requests never touch the network: TestClient calls the app in-process.
    APP_ENV is forced to "test" and the settings cache is cleared so every
    test gets an app built from test settings.
    """
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
