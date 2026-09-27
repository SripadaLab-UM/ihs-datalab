"""What went wrong with a model request, and whether to try it again.

The relay is the one place that retries a model request, and only before any
of the response has reached Codex: until then the request can be sent again
exactly as it was, and nothing the agent did depends on it. Codex's own
retries are kept to one (codex_config.py), so the attempts don't multiply.

A failure is sorted into a few kinds, because they need different handling:
a busy service is worth waiting for; a used-up allowance, a missing model or
a refused key is not, and retrying only burns requests. What is recorded
about it is metadata (status, a provider code, a wait), never a request or
response body.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx

Kind = Literal["busy", "quota", "model_unavailable", "auth", "request", "connection"]

RETRYABLE: frozenset[Kind] = frozenset({"busy", "connection"})

# Bounds on retrying one request (tuning values, not measured service limits).
# So one request from Codex costs at most MAX_ATTEMPTS upstream calls, at
# least MIN_DELAY apart (a `Retry-After: 0`, or none, isn't a reason to send
# again at once) and within BUDGET_SECONDS of waiting. With Codex's one retry
# of its own, that's at most 2 x MAX_ATTEMPTS calls for one model request.
MAX_ATTEMPTS = 3
BUDGET_SECONDS = 60.0
MIN_DELAY = 1.0
FIRST_DELAY = 2.0
MAX_DELAY = 30.0

# Provider codes for a used-up allowance. Only the code decides, never the
# message: Azure's ordinary rate-limit message links to ".../quotaincrease".
_QUOTA_CODES = ("insufficient_quota", "quota_exceeded", "billing_hard_limit", "spend_limit")
_CODE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


@dataclass(frozen=True)
class Trouble:
    kind: Kind
    status: int | None
    code: str | None
    retry_after: float | None
    request_id: str | None

    def record(self) -> dict[str, object]:
        """The metadata that's kept and shown: never a body or a header value
        other than the request ID."""
        return {
            "kind": self.kind,
            "status": self.status,
            "code": self.code,
            "retry_after": self.retry_after,
            "request_id": self.request_id,
        }


def classify(status: int, headers: httpx.Headers, body: bytes) -> Trouble:
    code = _provider_code(body)
    lowered = (code or "").lower()
    wait = retry_after(headers)
    if status == 429 or status == 403:
        # A server that says when to come back is busy, not out of allowance.
        if wait is None and any(word in lowered for word in _QUOTA_CODES):
            kind: Kind = "quota"
        else:
            kind = "busy" if status == 429 else "auth"
    elif status == 401:
        kind = "auth"
    elif status == 404:
        kind = "model_unavailable"
    elif status in (408, 409) or status >= 500:
        kind = "busy"
    else:
        kind = "request"
    request_id = headers.get("x-request-id")
    return Trouble(
        kind=kind,
        status=status,
        code=code,
        retry_after=wait,
        request_id=request_id if request_id and _CODE.match(request_id) else None,
    )


def connection_trouble(error: httpx.HTTPError) -> Trouble:
    return Trouble("connection", None, type(error).__name__, None, None)


def retry_after(headers: httpx.Headers, now: datetime | None = None) -> float | None:
    """The server's wait, from `retry-after-ms` or `Retry-After` (seconds or an
    HTTP date). None when absent or unreadable."""
    if (ms := headers.get("retry-after-ms")) is not None:
        try:
            return max(0.0, float(ms) / 1000)
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def next_delay(trouble: Trouble, attempt: int, waited: float) -> float | None:
    """How long to wait before attempt `attempt + 1`, or None to stop.

    A server's wait is never shortened: if it doesn't fit in what's left of
    the budget, the relay stops and says so, rather than retrying early. Nor
    is any wait shorter than MIN_DELAY.
    """
    if trouble.kind not in RETRYABLE or attempt >= MAX_ATTEMPTS:
        return None
    if trouble.retry_after is not None:
        delay = trouble.retry_after
    else:
        delay = min(MAX_DELAY, FIRST_DELAY * 2 ** (attempt - 1))
        delay *= random.uniform(0.8, 1.2)  # jitter, so sessions don't retry in step
    delay = max(MIN_DELAY, delay)
    return delay if waited + delay <= BUDGET_SECONDS else None


def _error_object(body: bytes) -> dict:
    try:
        parsed = json.loads(body[:16_384])
    except ValueError:
        return {}
    error = parsed.get("error") if isinstance(parsed, dict) else None
    return error if isinstance(error, dict) else {}


def _provider_code(body: bytes) -> str | None:
    error = _error_object(body)
    for key in ("code", "type"):
        value = error.get(key)
        if isinstance(value, str) and _CODE.match(value):
            return value
    return None
