"""Operational diagnostics: request log, latency/error metrics, eval summary, config snapshot."""
from __future__ import annotations

import json
from typing import Optional

from app.config import path, public_snapshot
from app.data import db


def recent_requests(limit: int = 100) -> list[dict]:
    return db.query("SELECT ts,request_id,operation,status,duration_ms,tokens_in,tokens_out,error FROM request_log "
                    "ORDER BY id DESC LIMIT ?", (limit,))


def ops_metrics() -> dict:
    rows = db.query("SELECT status,duration_ms,tokens_in,tokens_out FROM request_log WHERE operation='process_listing'")
    n = len(rows)
    lat = sorted(r["duration_ms"] for r in rows)
    return {
        "requests": n,
        "error_rate": round(sum(r["status"] == "error" for r in rows) / n, 4) if n else 0.0,
        "avg_latency_ms": round(sum(lat) / n, 1) if n else 0.0,
        "p95_latency_ms": lat[min(n - 1, int(0.95 * n))] if n else 0,
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
    }


def latest_eval_summary() -> Optional[dict]:
    p = path("eval_results") / "latest.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def get_config_snapshot() -> dict:
    from app.services.workflow import llm_status
    snap = public_snapshot()
    snap["llm_effective"] = llm_status()
    return snap
