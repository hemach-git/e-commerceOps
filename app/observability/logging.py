"""Structured JSON logging + request tracking (request_id, duration, status, tokens)."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app.config import path


def new_request_id(prefix: str = "req") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _mask(v: Any) -> Any:
    from app.services.guardrails import mask_sensitive  # local import avoids cycle
    if isinstance(v, str):
        return mask_sensitive(v)
    if isinstance(v, dict):
        return {k: _mask(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask(x) for x in v]
    return v


def log_event(event: str, **fields: Any) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **_mask(fields)}
    p = path("log")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


class Tracker:
    def __init__(self, operation: str, request_id: str):
        self.operation = operation
        self.request_id = request_id
        self.tokens_in = 0
        self.tokens_out = 0
        self.extra: dict[str, Any] = {}

    def add_tokens(self, tin: int, tout: int) -> None:
        self.tokens_in += int(tin or 0)
        self.tokens_out += int(tout or 0)


@contextmanager
def track(operation: str, request_id: str | None = None, **ctx: Any) -> Iterator[Tracker]:
    """Times an operation; writes a JSON log line and a request_log row (status ok/error)."""
    t = Tracker(operation, request_id or new_request_id())
    start = time.perf_counter()
    status, err = "ok", None
    try:
        yield t
    except Exception as e:  # re-raised after logging
        status, err = "error", f"{type(e).__name__}: {e}"
        raise
    finally:
        dur = int((time.perf_counter() - start) * 1000)
        log_event("request", request_id=t.request_id, operation=operation, status=status, duration_ms=dur,
                  tokens_in=t.tokens_in, tokens_out=t.tokens_out, error=err, **ctx, **t.extra)
        try:
            from app.data import db
            with db.tx() as c:
                c.execute(
                    "INSERT INTO request_log(ts,request_id,operation,status,duration_ms,tokens_in,tokens_out,error)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), t.request_id, operation, status,
                     dur, t.tokens_in, t.tokens_out, _mask(err) if err else None),
                )
        except Exception as log_err:  # never let telemetry break the request
            log_event("request_log_write_failed", error=str(log_err))
