"""HTTP client for the Google Health API v4.

Everything the pipeline reads goes through :meth:`HealthApiClient.iter_pages`,
which handles the reconciled stream, pagination, throttling, and retries.

Rate limits: Google enforces daily, per minute, and per user quotas and answers
429 on breach, but does not publish the numbers in the discovery document. So
this client throttles itself to a configurable requests per minute, backs off
exponentially on 429 and 5xx, and honors Retry-After when it is present.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import date
from typing import Any, Callable, Iterator

import httpx

from fitbit_pipeline.auth import CredentialStore
from fitbit_pipeline.datatypes import DataType

log = logging.getLogger(__name__)

BASE_URL = "https://health.googleapis.com/v4"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Statuses that mean "this data type does not support :reconcile", as opposed to
# a transient problem. The client falls back to :list for the rest of the run.
RECONCILE_UNSUPPORTED = {400, 404, 405, 501}


class ApiError(Exception):
    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class RateLimiter:
    """Minimum spacing between requests, so a backfill stays under quota."""

    def __init__(self, requests_per_minute: int, sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval = 60.0 / max(1, requests_per_minute)
        self._sleep = sleep
        self._last = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        gap = self.min_interval - (now - self._last)
        if gap > 0:
            self._sleep(gap)
        self._last = time.monotonic()


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class HealthApiClient:
    def __init__(
        self,
        credentials: CredentialStore,
        *,
        base_url: str = BASE_URL,
        data_source_family: str = "all-sources",
        max_requests_per_minute: int = 60,
        max_retries: int = 6,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.data_source_family = data_source_family
        self.max_retries = max_retries
        self._sleep = sleep
        self._limiter = RateLimiter(max_requests_per_minute, sleep)
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._method_override: dict[str, str] = {}
        self.api_calls = 0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "HealthApiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- transport ------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        refreshed = False
        while True:
            self._limiter.wait()
            headers = {
                "Authorization": f"Bearer {self.credentials.token()}",
                "Accept": "application/json",
            }
            self.api_calls += 1
            response = self._client.request(
                method, url, params=params, json=json_body, headers=headers
            )

            if response.status_code < 300:
                return response.json() if response.content else {}

            # A 401 on a token we believed valid means the access token died
            # early. Refresh once, then treat a repeat as a real failure.
            if response.status_code == 401 and not refreshed:
                refreshed = True
                log.info("access token rejected, refreshing")
                self.credentials.refresh()
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                delay = self._retry_delay(response, attempt)
                log.warning(
                    "retrying after API error",
                    extra={
                        "status": response.status_code,
                        "attempt": attempt + 1,
                        "delay_seconds": round(delay, 2),
                        "url": url,
                    },
                )
                self._sleep(delay)
                attempt += 1
                continue

            raise ApiError(
                response.status_code,
                f"{method} {url} failed with {response.status_code}: "
                f"{response.text[:500]}",
                response.text,
            )

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 300.0)
            except ValueError:
                pass
        # Exponential with jitter, capped so a stuck quota does not sleep forever.
        return min(2.0**attempt, 60.0) + random.uniform(0, 1.0)

    # --- account endpoints ----------------------------------------------

    def get_profile(self) -> dict[str, Any]:
        return self._request("GET", "users/me/profile")

    def get_settings(self) -> dict[str, Any]:
        return self._request("GET", "users/me/settings")

    def get_identity(self) -> dict[str, Any]:
        return self._request("GET", "users/me/identity")

    def list_paired_devices(self) -> dict[str, Any]:
        return self._request("GET", "users/me/pairedDevices")

    # --- data points -----------------------------------------------------

    def iter_pages(
        self,
        data_type: DataType,
        start: date,
        end: date,
        *,
        prefer_reconciled: bool = True,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield (method_used, page) for [start, end), following nextPageToken.

        Reconcile merges overlapping sources into the single stream the Fitbit
        app shows, so it is the default. Data types the API will not reconcile
        fall back to list, once, and the choice is remembered for the run.
        """
        use_reconcile = (
            prefer_reconciled
            and data_type.supports_reconcile
            and self._method_override.get(data_type.api_id) != "list"
        )
        page_token: str | None = None
        page_index = 0

        while True:
            params: dict[str, Any] = {
                "filter": data_type.build_filter(start, end),
                "pageSize": data_type.page_size,
            }
            if page_token:
                params["pageToken"] = page_token

            path = f"users/me/dataTypes/{data_type.api_id}/dataPoints"
            if use_reconcile:
                path += ":reconcile"
                params["dataSourceFamily"] = (
                    f"users/me/dataSourceFamilies/{self.data_source_family}"
                )

            try:
                page = self._request("GET", path, params=params)
            except ApiError as exc:
                if use_reconcile and exc.status in RECONCILE_UNSUPPORTED:
                    log.info(
                        "reconcile unavailable, falling back to list",
                        extra={"data_type": data_type.api_id, "status": exc.status},
                    )
                    self._method_override[data_type.api_id] = "list"
                    use_reconcile = False
                    page_token = None
                    page_index = 0
                    continue
                raise

            yield ("reconcile" if use_reconcile else "list"), page
            page_index += 1
            page_token = page.get("nextPageToken")
            if not page_token:
                return

    def daily_roll_up(
        self, data_type: DataType, start: date, end: date
    ) -> dict[str, Any]:
        """Server side daily aggregation, for spot checking against the app."""
        return self._request(
            "POST",
            f"users/me/dataTypes/{data_type.api_id}/dataPoints:dailyRollUp",
            json_body={
                "civilStartDate": {
                    "year": start.year,
                    "month": start.month,
                    "day": start.day,
                },
                "civilEndDate": {"year": end.year, "month": end.month, "day": end.day},
            },
        )


def data_points(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Both list and reconcile answer with a dataPoints array."""
    return page.get("dataPoints") or []
