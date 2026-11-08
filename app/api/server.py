"""Catalog Inspection API (FastAPI). Read-mostly; reuses the same service layer as the UI.

Run:  python -m uvicorn app.api.server:app --port 8000      Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import get_config
from app.observability.logging import log_event, new_request_id
from app.services import analytics, diagnostics, workflow
from app.services.rules import REASON_CODES

Status = Literal["DRAFT", "QUARANTINED", "PUBLISHED", "REJECTED"]

@asynccontextmanager
async def lifespan(_app):
    workflow.init_app()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Catalog Inspection API",
    version=get_config()["app"]["version"],
    description="Inspect raw vs normalised listings, validation errors, quarantine reasons and catalog state. "
                "All data is SYNTHETIC.",
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or new_request_id("api")
    t0 = time.perf_counter()
    status = 500
    try:
        resp = await call_next(request)
        status = resp.status_code
        resp.headers["X-Request-ID"] = rid
        return resp
    finally:
        log_event("api_request", request_id=rid, method=request.method, path=request.url.path, http_status=status,
                  duration_ms=int((time.perf_counter() - t0) * 1000))


class SubmitListing(BaseModel):
    supplier_id: str
    supplier_sku: str
    raw_title: str
    raw_bullets: str = ""
    raw_size_tag: str = ""
    raw_color_tag: str = ""
    raw_price: str = ""


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm": workflow.llm_status(), "data_label": get_config()["app"]["data_label"]}


@app.get("/categories")
def categories(leaf_only: bool = False) -> list[dict]:
    rows = workflow.list_categories()
    return [r for r in rows if r["is_leaf"]] if leaf_only else rows


@app.get("/listings")
def listings(status: Optional[Status] = None, reason: Optional[str] = Query(None, max_length=40),
             category_id: Optional[str] = Query(None, max_length=40), limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    return workflow.list_listings(status=status, reason=reason, category_id=category_id, limit=limit)


@app.get("/listings/{listing_id}")
def listing_detail(listing_id: int) -> dict:
    d = workflow.get_listing_detail(listing_id)
    if not d:
        raise HTTPException(404, f"listing {listing_id} not found")
    return d


@app.get("/quarantine")
def quarantine(reason: Optional[str] = Query(None, max_length=40), limit: int = Query(100, ge=1, le=1000)) -> dict:
    rows = workflow.list_listings(status="QUARANTINED", reason=reason, limit=limit)
    return {"count": len(rows), "reason_filter": reason, "reason_codes": REASON_CODES, "items": rows}


@app.get("/catalog")
def catalog() -> list[dict]:
    return workflow.list_live_catalog()


@app.get("/feeds/{feed_id}")
def feed(feed_id: int) -> dict:
    rows = [f for f in workflow.list_feeds() if f["feed_id"] == feed_id]
    if not rows:
        raise HTTPException(404, f"feed {feed_id} not found")
    return rows[0]


@app.get("/metrics")
def metrics() -> dict:
    return {"kpis": analytics.get_kpis(), "quarantine_reasons": analytics.quarantine_reasons(),
            "ops": diagnostics.ops_metrics(), "latest_eval": diagnostics.latest_eval_summary()}


@app.get("/runs")
def runs() -> list[dict]:
    return analytics.run_history()


@app.post("/listings/validate", status_code=201)
def submit(body: SubmitListing) -> dict:
    """Submit one raw listing through the full pipeline (lands in DRAFT or QUARANTINE, never live)."""
    try:
        return workflow.submit_listing(**body.model_dump(), actor="api").model_dump()
    except ValueError as e:
        raise HTTPException(422, str(e))
