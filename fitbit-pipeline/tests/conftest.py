"""Shared test fixtures.

The API is exercised through httpx.MockTransport against recorded response
shapes, so the whole ingestion path runs in tests without a network or a token.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from fitbit_pipeline import db
from fitbit_pipeline.api import HealthApiClient

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class FakeCredentials:
    """Stands in for CredentialStore. Never touches the network."""

    def __init__(self) -> None:
        self.refreshes = 0
        self.account_email = "owner@example.com"

    def token(self) -> str:
        return "fake-access-token"

    def refresh(self) -> None:
        self.refreshes += 1


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "test.sqlite3")
    yield connection
    connection.close()


@pytest.fixture
def make_client() -> Callable[..., HealthApiClient]:
    """Build a client whose transport is driven by a handler function."""

    def factory(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> HealthApiClient:
        transport = httpx.MockTransport(handler)
        return HealthApiClient(
            FakeCredentials(),
            client=httpx.Client(transport=transport),
            sleep=lambda _seconds: None,
            **kwargs,
        )

    return factory


@pytest.fixture
def fixture_client(make_client):
    """Serve recorded pages keyed by data type, honoring pagination."""

    def factory(pages_by_type: dict[str, list[dict[str, Any]]], **kwargs: Any) -> HealthApiClient:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            segments = request.url.path.split("/")
            data_type = segments[segments.index("dataTypes") + 1]
            pages = pages_by_type.get(data_type, [{"dataPoints": []}])
            token = request.url.params.get("pageToken")
            index = int(token.split(":")[-1]) if token else 0
            page = dict(pages[index]) if index < len(pages) else {"dataPoints": []}
            if index + 1 < len(pages):
                page["nextPageToken"] = f"page:{index + 1}"
            else:
                page.pop("nextPageToken", None)
            return httpx.Response(200, json=page)

        client = make_client(handler, **kwargs)
        client.recorded_calls = calls  # type: ignore[attr-defined]
        return client

    return factory
