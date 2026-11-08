# Data model

SQLite with `PRAGMA foreign_keys = ON` on every connection (tested: inserting a violation for a non-existent listing, or a listing with an unknown category, raises `IntegrityError`). Every write passes through Pydantic first. CHECK constraints guard status enums, confidence range, positive live price.

```mermaid
erDiagram
    canonical_categories ||--o{ canonical_categories : "parent_id"
    canonical_categories ||--o{ processed_listings : "category_id"
    canonical_categories ||--o{ live_catalog : "category_id"
    incoming_supplier_feeds ||--o| processed_listings : "feed_id (UNIQUE)"
    pipeline_runs ||--o{ processed_listings : "run_id"
    processed_listings ||--o{ listing_variations : "listing_id (CASCADE)"
    processed_listings ||--o{ rule_violations : "listing_id (CASCADE)"
    processed_listings ||--o| live_catalog : "listing_id (UNIQUE)"

    canonical_categories {
        TEXT category_id PK
        TEXT name
        TEXT parent_id FK
        TEXT required_attributes_json
        TEXT prohibited_keywords
    }
    incoming_supplier_feeds {
        INTEGER feed_id PK
        TEXT supplier_id
        TEXT supplier_sku
        TEXT raw_title
        TEXT raw_bullets
        TEXT raw_size_tag
        TEXT raw_color_tag
        TEXT raw_price
        TEXT status "PENDING|PROCESSED"
        INTEGER is_synthetic
    }
    pipeline_runs {
        TEXT run_id PK
        TEXT provider
        TEXT model
        TEXT config_snapshot_json
        INTEGER total
        INTEGER drafted
        INTEGER quarantined
        INTEGER errors
    }
    processed_listings {
        INTEGER listing_id PK
        INTEGER feed_id FK
        TEXT run_id FK
        TEXT request_id
        TEXT clean_title
        TEXT category_id FK
        REAL category_confidence
        REAL price
        TEXT attributes_json
        TEXT llm_raw_json
        INTEGER extraction_attempts
        TEXT status "DRAFT|QUARANTINED|PUBLISHED|REJECTED"
    }
    listing_variations {
        INTEGER variation_id PK
        INTEGER listing_id FK
        TEXT size
        TEXT color
        TEXT sku
    }
    rule_violations {
        INTEGER violation_id PK
        INTEGER listing_id FK
        TEXT code
        TEXT severity "BLOCK|WARN"
        TEXT field
        TEXT detail
    }
    live_catalog {
        INTEGER catalog_id PK
        INTEGER listing_id FK
        TEXT category_id FK
        TEXT title
        REAL price
        TEXT published_by
    }
    audit_log {
        INTEGER audit_id PK
        TEXT ts
        TEXT actor
        TEXT action
        TEXT entity_id
        TEXT decision
        TEXT model
        TEXT request_id
    }
    request_log {
        INTEGER id PK
        TEXT request_id
        TEXT operation
        TEXT status
        INTEGER duration_ms
        INTEGER tokens_in
        INTEGER tokens_out
    }
```

## `required_attributes_json` format
```json
[{"name": "size", "type": "enum_list", "allowed": ["XS","S","M","L","XL","XXL","3XL"]},
 {"name": "colors", "type": "list"},
 {"name": "gender", "type": "enum", "allowed": ["Men"]},
 {"name": "material", "type": "str", "required": false}]
```
Types: `str`, `int` (min/max), `enum`, `enum_list`, `list`. Validated by `AttributeSpec`, compiled into a per-category Pydantic model by `build_attribute_model()`.

## Reason codes
| Code | Severity | Source |
|---|---|---|
| PROMPT_INJECTION_DETECTED | BLOCK | guardrails |
| PII_IN_LISTING | BLOCK | guardrails |
| INPUT_TOO_LONG | BLOCK | guardrails |
| CLASSIFICATION_INVALID / EXTRACTION_INVALID | BLOCK | schema validation after retries |
| LOW_CONFIDENCE | BLOCK | rules (threshold 0.70) |
| PROHIBITED_KEYWORD | BLOCK | rules (global + category list, word boundary) |
| MISSING_REQUIRED_ATTR / INVALID_ATTR_VALUE | BLOCK | dynamic attribute model |
| INVALID_PRICE | BLOCK | rules |
| TITLE_TOO_SHORT | BLOCK | rules |
| TITLE_TRUNCATED / SHOUTING_TITLE | WARN | rules (auto-fixed) |
