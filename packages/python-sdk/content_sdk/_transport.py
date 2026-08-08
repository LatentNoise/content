"""HTTP transport: one httpx-based layer for both the sync and async clients.

Responsibilities: build `/api/v1` URLs, map non-2xx → typed exceptions, and
apply a **conservative** retry policy. Retries happen ONLY on transport-level
failures (the request provably did not get a response) and on 5xx for **safe**
methods (GET). Creations (`POST /analyses`, `POST /jobs`) are never retried
unless the caller explicitly opts in via an idempotency mechanism.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import httpx

from .errors import TransportError, error_for

DEFAULT_BASE_URL = "http://localhost:8010"
DEFAULT_TIMEOUT = 130.0


@dataclass(frozen=True)
class RetryConfig:
    retries: int = 2  # extra attempts after the first
    backoff: float = 0.2  # seconds; doubled each attempt


def resolve_base_url(base_url: str | None) -> str:
    return (base_url or os.getenv("CONTENT_API_URL", DEFAULT_BASE_URL)).rstrip("/")


def _api_url(base_url: str, path: str) -> str:
    return f"{base_url}/api/v1{path}"


def _raise_for(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        body: object = resp.json()
    except ValueError:
        body = resp.text
    raise error_for(resp.status_code, body)


def _attempts(method: str, retry: RetryConfig, idempotent: bool) -> int:
    safe = method.upper() == "GET"
    return retry.retries if (safe or idempotent) else 0


def _retry_status(status: int) -> bool:
    return status >= 500


class SyncTransport:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        retry: RetryConfig,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url
        self._retry = retry
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = _api_url(self.base_url, path)
        attempts = _attempts(method, self._retry, idempotent)
        for attempt in range(attempts + 1):
            try:
                resp = self._client.request(method, url, json=json, params=params)
            except httpx.TransportError as exc:
                if attempt < attempts:
                    time.sleep(self._retry.backoff * (2**attempt))
                    continue
                raise TransportError(str(exc)) from exc
            if _retry_status(resp.status_code) and attempt < attempts:
                time.sleep(self._retry.backoff * (2**attempt))
                continue
            _raise_for(resp)
            return resp
        raise TransportError("retries exhausted")  # pragma: no cover

    def get(self, path: str, params: dict | None = None) -> object:
        return self.request("GET", path, params=params).json()

    def post(
        self, path: str, json: dict | None = None, idempotent: bool = False
    ) -> object:
        return self.request("POST", path, json=json, idempotent=idempotent).json()

    def content(self, path: str) -> bytes:
        return self.request("GET", path).content


class AsyncTransport:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        retry: RetryConfig,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url
        self._retry = retry
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = _api_url(self.base_url, path)
        attempts = _attempts(method, self._retry, idempotent)
        for attempt in range(attempts + 1):
            try:
                resp = await self._client.request(method, url, json=json, params=params)
            except httpx.TransportError as exc:
                if attempt < attempts:
                    await asyncio.sleep(self._retry.backoff * (2**attempt))
                    continue
                raise TransportError(str(exc)) from exc
            if _retry_status(resp.status_code) and attempt < attempts:
                await asyncio.sleep(self._retry.backoff * (2**attempt))
                continue
            _raise_for(resp)
            return resp
        raise TransportError("retries exhausted")  # pragma: no cover

    async def get(self, path: str, params: dict | None = None) -> object:
        return (await self.request("GET", path, params=params)).json()

    async def post(
        self, path: str, json: dict | None = None, idempotent: bool = False
    ) -> object:
        return (
            await self.request("POST", path, json=json, idempotent=idempotent)
        ).json()

    async def content(self, path: str) -> bytes:
        return (await self.request("GET", path)).content
