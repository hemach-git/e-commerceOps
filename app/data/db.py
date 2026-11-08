"""SQLite access layer. Foreign keys are enforced on every connection."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import db_path

SCHEMA = """
-- ===== Starter tables (given) =====
CREATE TABLE IF NOT EXISTS canonical_categories (
    category_id              TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    parent_id                TEXT REFERENCES canonical_categories(category_id),
    required_attributes_json TEXT NOT NULL DEFAULT '[]',
    prohibited_keywords      TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS incoming_supplier_feeds (
    feed_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id    TEXT NOT NULL,
    supplier_sku   TEXT NOT NULL,
    raw_title      TEXT NOT NULL,
    raw_bullets    TEXT NOT NULL DEFAULT '',
    raw_size_tag   TEXT NOT NULL DEFAULT '',
    raw_color_tag  TEXT NOT NULL DEFAULT '',
    raw_price      TEXT NOT NULL DEFAULT '',
    received_at    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSED')),
    is_synthetic   INTEGER NOT NULL DEFAULT 1 CHECK (is_synthetic IN (0,1)),
    UNIQUE (supplier_id, supplier_sku)
);

-- ===== Pipeline tables =====
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id               TEXT PRIMARY KEY,
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    actor                TEXT NOT NULL,
    provider             TEXT NOT NULL,
    model                TEXT NOT NULL,
    config_snapshot_json TEXT NOT NULL,
    total                INTEGER NOT NULL DEFAULT 0,
    drafted              INTEGER NOT NULL DEFAULT 0,
    quarantined          INTEGER NOT NULL DEFAULT 0,
    errors               INTEGER NOT NULL DEFAULT 0,
    duration_ms          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS processed_listings (
    listing_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id                 INTEGER NOT NULL UNIQUE REFERENCES incoming_supplier_feeds(feed_id),
    run_id                  TEXT REFERENCES pipeline_runs(run_id),
    request_id              TEXT NOT NULL,
    clean_title             TEXT,
    brand                   TEXT,
    product_type            TEXT,
    gender                  TEXT,
    material                TEXT,
    pack_count              INTEGER CHECK (pack_count IS NULL OR pack_count >= 1),
    category_id             TEXT REFERENCES canonical_categories(category_id),
    category_confidence     REAL CHECK (category_confidence IS NULL OR (category_confidence BETWEEN 0 AND 1)),
    category_rationale      TEXT,
    price                   REAL,
    attributes_json         TEXT NOT NULL DEFAULT '{}',
    llm_raw_json            TEXT NOT NULL DEFAULT '{}',
    extraction_attempts     INTEGER NOT NULL DEFAULT 0,
    classification_attempts INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL CHECK (status IN ('DRAFT','QUARANTINED','PUBLISHED','REJECTED')),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listing_variations (
    variation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL REFERENCES processed_listings(listing_id) ON DELETE CASCADE,
    size         TEXT,
    color        TEXT,
    sku          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_violations (
    violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   INTEGER NOT NULL REFERENCES processed_listings(listing_id) ON DELETE CASCADE,
    code         TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('BLOCK','WARN')),
    field        TEXT,
    detail       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS live_catalog (
    catalog_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id      INTEGER NOT NULL UNIQUE REFERENCES processed_listings(listing_id),
    category_id     TEXT NOT NULL REFERENCES canonical_categories(category_id),
    title           TEXT NOT NULL,
    price           REAL NOT NULL CHECK (price > 0),
    attributes_json TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    published_by    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    decision     TEXT,
    model        TEXT,
    request_id   TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS request_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    operation   TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('ok','error')),
    duration_ms INTEGER NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS ix_listings_status ON processed_listings(status);
CREATE INDEX IF NOT EXISTS ix_violations_listing ON rule_violations(listing_id);
CREATE INDEX IF NOT EXISTS ix_violations_code ON rule_violations(code);
CREATE INDEX IF NOT EXISTS ix_request_log_ts ON request_log(ts);
"""


def connect() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Transaction: commit on success, rollback on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with tx() as c:
        c.executescript(SCHEMA)


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: tuple | list = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None
