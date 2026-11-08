"""Lightweight audit trail: who, what, when, decision, model, request_id."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.data import db
from app.models.schemas import utcnow


def record(conn, *, actor: str, action: str, entity_type: str, entity_id: Any, decision: Optional[str] = None,
           model: Optional[str] = None, request_id: Optional[str] = None, details: Optional[dict] = None) -> None:
    from app.services.guardrails import mask_sensitive
    conn.execute(
        "INSERT INTO audit_log(ts,actor,action,entity_type,entity_id,decision,model,request_id,details_json)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (utcnow(), actor, action, entity_type, str(entity_id) if entity_id is not None else None, decision, model,
         request_id, mask_sensitive(json.dumps(details or {}, default=str))),
    )


def list_audit(limit: int = 200, entity_id: Optional[str] = None) -> list[dict]:
    if entity_id is not None:
        rows = db.query("SELECT * FROM audit_log WHERE entity_id=? ORDER BY audit_id DESC LIMIT ?", (str(entity_id), limit))
    else:
        rows = db.query("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (limit,))
    for r in rows:
        r["details"] = json.loads(r.pop("details_json") or "{}")
    return rows
