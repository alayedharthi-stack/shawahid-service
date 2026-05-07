"""
exam_engine.sources.http_client — pluggable, safe HTTP for providers.

Phase-11 contract
=================
Concrete providers fetch their pages through an ``HttpClient``. The
DEFAULT client is ``DisabledHttpClient`` — it does no network IO and
returns ``None``. Real network access is opt-in:

    • set the env var ``EXAM_SOURCES_NETWORK_ENABLED=1`` AND
    • inject a ``RequestsHttpClient`` (only available when the
      ``requests`` package is installed)

This means the system is *safe by default* — running tests, dev,
or production never hits the public internet unless an operator
explicitly turns it on.

Pure module aside from optional ``requests`` import.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────


class SourceUnavailable(Exception):
    """Raised when a provider's fetch path cannot complete safely.

    The engine catches this and treats the provider as empty — it does
    NOT propagate to the caller. This keeps a single misbehaving
    source from taking down the whole exam pipeline.
    """


# ──────────────────────────────────────────────────────────────────────
# Result DTO
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HttpResponse:
    """A minimal HTTP response abstraction.

    Providers only need ``status`` and ``body`` — no streaming, no
    headers leakage. Tests build these directly.
    """

    status: int
    body: str
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


# ──────────────────────────────────────────────────────────────────────
# Interface
# ──────────────────────────────────────────────────────────────────────


@runtime_checkable
class HttpClient(Protocol):
    """Tiny request interface every concrete client implements."""

    def get(self, url: str, *, timeout: float = 10.0) -> HttpResponse | None:
        ...


# ──────────────────────────────────────────────────────────────────────
# Default: do nothing
# ──────────────────────────────────────────────────────────────────────


class DisabledHttpClient:
    """Default client. Always returns ``None`` — never hits network.

    This is the *safe by default* posture: every provider sees an
    empty source instead of attempting an outbound request. Operators
    enable real fetching by injecting ``RequestsHttpClient`` once
    they've completed the legal/quality review.
    """

    name = "disabled"

    def get(self, url: str, *, timeout: float = 10.0) -> HttpResponse | None:
        logger.debug("[HTTP DISABLED] would fetch %r", url)
        return None


# ──────────────────────────────────────────────────────────────────────
# Real network client (lazy import of `requests`)
# ──────────────────────────────────────────────────────────────────────


class RequestsHttpClient:
    """Real HTTP client built on top of the ``requests`` library.

    Features:
        • per-request timeout
        • configurable retry with exponential backoff
        • size cap to avoid memory blow-ups on huge pages
        • returns ``None`` (never raises) on any error so providers
          stay in their "fail-soft" lane
    """

    name = "requests"

    def __init__(
        self,
        *,
        max_retries: int = 2,
        backoff_base: float = 0.4,
        max_body_bytes: int = 2 * 1024 * 1024,  # 2 MB
        user_agent: str = "Shawahid-AI/exam-source-bot (+https://example.invalid)",
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_body_bytes = max_body_bytes
        self.user_agent = user_agent
        self._session = None  # lazy

    def _get_session(self):
        if self._session is None:
            try:
                import requests  # type: ignore
            except ImportError as exc:
                raise SourceUnavailable(
                    f"requests is not installed: {exc}"
                ) from exc
            sess = requests.Session()
            sess.headers["User-Agent"] = self.user_agent
            self._session = sess
        return self._session

    def get(self, url: str, *, timeout: float = 10.0) -> HttpResponse | None:
        if not _network_enabled():
            logger.info(
                "[HTTP] network disabled by env (EXAM_SOURCES_NETWORK_ENABLED), "
                "skipping %s", url,
            )
            return None

        try:
            sess = self._get_session()
        except SourceUnavailable as exc:
            logger.warning("[HTTP] %s", exc)
            return None

        for attempt in range(self.max_retries + 1):
            try:
                resp = sess.get(url, timeout=timeout)
                # Cap body size — refuse anything larger.
                body = resp.text
                if len(body.encode("utf-8", "ignore")) > self.max_body_bytes:
                    logger.warning("[HTTP] body too large, dropping: %s", url)
                    return None
                return HttpResponse(
                    status=resp.status_code, body=body, url=url,
                )
            except Exception as exc:  # noqa: BLE001
                if attempt < self.max_retries:
                    delay = self.backoff_base * (2 ** attempt)
                    logger.info(
                        "[HTTP] retry %d for %s after %s (delay=%.2fs)",
                        attempt + 1, url, exc, delay,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("[HTTP] giving up on %s: %s", url, exc)
                return None
        return None


# ──────────────────────────────────────────────────────────────────────
# In-memory client (tests inject canned bodies)
# ──────────────────────────────────────────────────────────────────────


class InMemoryHttpClient:
    """Test helper: maps URL → canned ``HttpResponse``.

    Falls back to ``None`` for unknown URLs. Tracks ``calls`` so tests
    can assert call counts after cache hits.
    """

    name = "in_memory"

    def __init__(self, mapping: dict[str, HttpResponse | str] | None = None) -> None:
        self._mapping: dict[str, HttpResponse] = {}
        for url, value in (mapping or {}).items():
            if isinstance(value, HttpResponse):
                self._mapping[url] = value
            else:
                self._mapping[url] = HttpResponse(status=200, body=str(value), url=url)
        self.calls: list[str] = []

    def add(self, url: str, body: str, *, status: int = 200) -> None:
        self._mapping[url] = HttpResponse(status=status, body=body, url=url)

    def get(self, url: str, *, timeout: float = 10.0) -> HttpResponse | None:
        self.calls.append(url)
        return self._mapping.get(url)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _network_enabled() -> bool:
    """Return True only when an operator explicitly turned network on."""
    val = os.environ.get("EXAM_SOURCES_NETWORK_ENABLED", "")
    return val.strip() in ("1", "true", "yes", "on")


def default_client() -> HttpClient:
    """Project-wide default. Currently ``DisabledHttpClient``.

    A future phase can flip this to a real client once the legal
    review lands; until then we stay safe by default.
    """
    return DisabledHttpClient()


__all__ = [
    "SourceUnavailable",
    "HttpResponse",
    "HttpClient",
    "DisabledHttpClient",
    "RequestsHttpClient",
    "InMemoryHttpClient",
    "default_client",
]
