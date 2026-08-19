import httpx
import pytest

from fitbit_pipeline.api import ApiError, HealthApiClient, RateLimiter
from fitbit_pipeline.datatypes import BY_ID
from datetime import date

START, END = date(2026, 8, 17), date(2026, 8, 18)


def test_reconcile_is_preferred_and_carries_the_source_family(fixture_client):
    client = fixture_client({"steps": [{"dataPoints": []}]}, data_source_family="google-wearables")
    list(client.iter_pages(BY_ID["steps"], START, END))
    request = client.recorded_calls[0]
    assert request.url.path.endswith("dataPoints:reconcile")
    assert (
        request.url.params["dataSourceFamily"]
        == "users/me/dataSourceFamilies/google-wearables"
    )
    assert request.url.params["pageSize"] == "10000"


def test_prefer_reconciled_false_uses_plain_list(fixture_client):
    client = fixture_client({"steps": [{"dataPoints": []}]})
    list(client.iter_pages(BY_ID["steps"], START, END, prefer_reconciled=False))
    assert client.recorded_calls[0].url.path.endswith("dataPoints")


def test_pagination_follows_next_page_token(fixture_client):
    pages = [{"dataPoints": [{"steps": {}}]} for _ in range(3)]
    client = fixture_client({"steps": pages})
    got = list(client.iter_pages(BY_ID["steps"], START, END))
    assert len(got) == 3
    assert client.recorded_calls[1].url.params["pageToken"] == "page:1"
    assert "pageToken" not in client.recorded_calls[0].url.params


def test_reconcile_falls_back_to_list_for_types_that_reject_it(make_client):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith(":reconcile"):
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return httpx.Response(200, json={"dataPoints": []})

    client = make_client(handler)
    pages = list(client.iter_pages(BY_ID["steps"], START, END))
    assert [method for method, _ in pages] == ["list"]
    assert seen[0].endswith(":reconcile")
    assert seen[1].endswith("dataPoints")

    # The fallback is remembered, so the next range does not retry reconcile.
    list(client.iter_pages(BY_ID["steps"], START, END))
    assert not seen[2].endswith(":reconcile")


def test_429_is_retried_with_backoff(make_client):
    attempts = {"n": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"dataPoints": []})

    client = make_client(handler)
    client._sleep = slept.append
    client._limiter._sleep = lambda _s: None
    list(client.iter_pages(BY_ID["steps"], START, END))
    assert attempts["n"] == 3
    assert len(slept) == 2
    assert slept[1] > slept[0]  # exponential


def test_retry_after_header_is_honored(make_client):
    slept: list[float] = []
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "12"}, json={})
        return httpx.Response(200, json={"dataPoints": []})

    client = make_client(handler)
    client._sleep = slept.append
    client._limiter._sleep = lambda _s: None
    list(client.iter_pages(BY_ID["steps"], START, END))
    assert slept == [12.0]


def test_retries_give_up_and_raise(make_client):
    client = make_client(lambda request: httpx.Response(503, json={}), max_retries=2)
    client._sleep = lambda _s: None
    client._limiter._sleep = lambda _s: None
    with pytest.raises(ApiError) as excinfo:
        list(client.iter_pages(BY_ID["steps"], START, END))
    assert excinfo.value.status == 503


def test_a_401_refreshes_the_token_once_then_fails(make_client):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"dataPoints": []})

    client = make_client(handler)
    list(client.iter_pages(BY_ID["steps"], START, END))
    assert client.credentials.refreshes == 1

    always_401 = make_client(lambda request: httpx.Response(401, json={}))
    with pytest.raises(ApiError):
        list(always_401.iter_pages(BY_ID["steps"], START, END))
    assert always_401.credentials.refreshes == 1  # refreshed once, not in a loop


def test_rate_limiter_spaces_requests():
    slept: list[float] = []
    limiter = RateLimiter(60, sleep=slept.append)
    limiter.wait()
    limiter.wait()
    assert limiter.min_interval == 1.0
    assert slept and slept[0] > 0
