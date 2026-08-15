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
from pathlib import Path

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


def _raise_for_stream(resp: httpx.Response) -> None:
    """`_raise_for` for a streamed response: the body must be read first, since
    a streaming response has none until asked, and an error body is small."""
    if resp.is_success:
        return
    resp.read()
    _raise_for(resp)


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
        files: dict | None = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = _api_url(self.base_url, path)
        attempts = _attempts(method, self._retry, idempotent)
        for attempt in range(attempts + 1):
            try:
                resp = self._client.request(
                    method, url, json=json, params=params, files=files
                )
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

    def post_file(self, path: str, source: Path, *, media_type: str = "") -> object:
        """POST one file as multipart, streamed from disk.

        Never retried, and it must not be: the body is a file handle the first
        attempt would already have consumed, and re-sending an upload is a
        decision for the caller, not for the transport.
        """
        with Path(source).open("rb") as handle:
            files = {"file": (Path(source).name, handle, media_type or None)}
            return self.request("POST", path, files=files).json()

    def post_bytes(
        self, path: str, filename: str, data: bytes, media_type: str = ""
    ) -> object:
        """POST bytes already in memory as multipart — for callers that never
        had a file on disk, such as a browser upload arriving through a UI."""
        files = {"file": (filename, data, media_type or None)}
        return self.request("POST", path, files=files).json()

    def stream_sse(self, path: str, params: dict | None = None):
        """Yield parsed Server-Sent Events until the server closes the stream.

        Two details make this work over a long job. The read timeout is
        disabled for this request only — the client's default would sever a
        stream that is merely quiet, which is exactly what a forty-minute
        download looks like between progress ticks — and `:` comment lines
        (the server's keep-alive) are skipped rather than parsed.

        Yields `{"id", "type", "data"}` with `data` still a string; the caller
        decides how to read it.
        """
        url = _api_url(self.base_url, path)
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        with self._client.stream("GET", url, params=params, timeout=timeout) as resp:
            _raise_for_stream(resp)
            event_type, data_lines, event_id = "message", [], None
            for line in resp.iter_lines():
                if line.startswith(":"):
                    continue  # keep-alive
                if not line:
                    if data_lines:
                        yield {
                            "id": event_id,
                            "type": event_type,
                            "data": "\n".join(data_lines),
                        }
                    event_type, data_lines, event_id = "message", [], None
                    continue
                field, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if field == "event":
                    event_type = value
                elif field == "data":
                    data_lines.append(value)
                elif field == "id":
                    event_id = value

    def content(self, path: str) -> bytes:
        return self.request("GET", path).content

    def stream_to(self, path: str, destination: Path) -> int:
        """Stream a response body into *destination*, returning bytes written.

        Deliberately not `content()` + `write_bytes()`: artifacts are media
        files, and holding a multi-gigabyte video in memory to copy it to disk
        is the kind of thing that works until someone downloads a real film.

        Written to a sibling `.part` and renamed on completion, so an
        interrupted transfer never leaves a truncated file wearing the real
        name. No retry: replaying a partial stream needs range requests, and
        silently resuming into a half-written file would be worse than failing.
        """
        url = _api_url(self.base_url, path)
        partial = destination.with_name(destination.name + ".part")
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with self._client.stream("GET", url) as response:
                _raise_for_stream(response)
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 256):
                        handle.write(chunk)
                        written += len(chunk)
        except httpx.TransportError as exc:
            partial.unlink(missing_ok=True)
            raise TransportError(str(exc)) from exc
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        os.replace(partial, destination)
        return written


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
        files: dict | None = None,
        idempotent: bool = False,
    ) -> httpx.Response:
        url = _api_url(self.base_url, path)
        attempts = _attempts(method, self._retry, idempotent)
        for attempt in range(attempts + 1):
            try:
                resp = await self._client.request(
                    method, url, json=json, params=params, files=files
                )
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

    async def post_file(
        self, path: str, source: Path, *, media_type: str = ""
    ) -> object:
        """POST one file as multipart. Never retried — see the sync twin.

        Unlike the sync twin this **buffers**: the bytes are read in a worker
        thread (blocking file I/O must not run on the event loop) and sent from
        memory, because handing httpx a blocking file handle would move the
        same stall inside the send. For a large upload prefer the sync client,
        which streams from disk and holds nothing.
        """
        payload = await asyncio.to_thread(Path(source).read_bytes)
        files = {"file": (Path(source).name, payload, media_type or None)}
        return (await self.request("POST", path, files=files)).json()

    async def post_bytes(
        self, path: str, filename: str, data: bytes, media_type: str = ""
    ) -> object:
        """POST bytes already in memory as multipart — the async twin."""
        files = {"file": (filename, data, media_type or None)}
        return (await self.request("POST", path, files=files)).json()

    async def content(self, path: str) -> bytes:
        return (await self.request("GET", path)).content
