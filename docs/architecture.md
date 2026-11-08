# Architecture

## Design principle
**The LLM proposes; Python disposes.** The model is used only where language understanding is needed (picking a category from a closed list, extracting facts from messy text). Every output is parsed and validated with Pydantic, retried with the validation error on failure, and then **all policy decisions** (required attributes, prohibited terms, price, PII, injection, confidence threshold, routing, publish gate) are made by deterministic Python. The LLM is never asked "is this compliant?".

## 1. System architecture
```mermaid
flowchart LR
    U[Catalog Ops / Reviewer] --> UI[Streamlit UI<br/>Ingest - Review - Catalog - Analytics - Diagnostics]
    EXT[Integrations / Engineers] --> API[FastAPI<br/>Catalog Inspection API]
    UI --> WF[services/workflow.py<br/>orchestration]
    API --> WF
    WF --> GR[guardrails.py<br/>length, injection, PII, delimiting]
    WF --> CL[classifier.py<br/>closed-enum taxonomy]
    WF --> EX[extractor.py<br/>strict schema]
    CL --> SV[structured.py<br/>JSON -> Pydantic -> retry]
    EX --> SV
    SV --> LLM["llm.py<br/>anthropic or mock"]
    WF --> NR[normalize.py<br/>sizes, colours, packs, titles]
    WF --> RU[rules.py<br/>attributes + compliance + routing]
    WF --> DB[(SQLite<br/>FK enforced)]
    WF --> AU[audit.py]
    WF --> OB[observability/logging.py<br/>JSON logs + request_log]
    UI --> AN[analytics.py / diagnostics.py]
    AN --> DB
```

## 2. Data flow
```mermaid
flowchart TD
    A[incoming_supplier_feeds<br/>raw, untrusted] --> B{Guardrails<br/>length / injection / PII}
    B -- BLOCK --> Q[(Quarantine)]
    B -- pass --> C[LLM classify<br/>category enum]
    C --> C1{Pydantic + in-taxonomy?}
    C1 -- invalid x3 --> Q
    C1 -- valid --> D[LLM extract<br/>ExtractedListing schema]
    D --> D1{Pydantic extra=forbid?}
    D1 -- invalid x3 --> Q
    D1 -- valid --> E[Deterministic normalise<br/>size/colour/pack/title]
    E --> F[Dynamic per-category attribute model<br/>from required_attributes_json]
    F --> G[Compliance rules<br/>prohibited terms, price, confidence]
    G --> H{Any BLOCK?}
    H -- yes --> Q
    H -- no --> DR[(Draft catalog)]
    DR --> R{Human review}
    R -- approve + publish gate --> L[(Live catalog)]
    R -- reject --> X[(Rejected)]
    Q --> R2{Human review}
    R2 -- reject --> X
    L --> AN[Analytics / API]
    Q --> AN
```

## 3. Process sequence
```mermaid
sequenceDiagram
    actor Ops as Catalog Ops
    participant UI as Streamlit UI
    participant WF as Workflow
    participant GR as Guardrails
    participant LLM as LLM (anthropic|mock)
    participant RU as Rules (Python)
    participant DB as SQLite
    participant AU as Audit
    Ops->>UI: Run batch
    UI->>WF: run_batch()
    WF->>DB: insert pipeline_runs (+config snapshot)
    loop each pending feed
        WF->>GR: check_input(raw)
        alt injection / PII / too long
            GR-->>WF: BLOCK violations (no LLM call)
        else clean
            WF->>LLM: classify (closed enum, delimited input)
            LLM-->>WF: raw JSON
            WF->>WF: Pydantic validate, retry with error (max 2)
            WF->>LLM: extract (strict schema)
            LLM-->>WF: raw JSON
            WF->>WF: Pydantic validate, retry with error (max 2)
            WF->>RU: normalise + validate attributes + compliance
        end
        RU-->>WF: violations + route (DRAFT|QUARANTINED)
        WF->>DB: listing, variations, violations (one transaction)
        WF->>AU: PIPELINE_ROUTE (request_id, model, reasons)
    end
    Ops->>UI: Approve draft
    UI->>WF: review_decision(APPROVE, reviewer)
    WF->>WF: publish gate (DRAFT, no BLOCKs, price>0)
    WF->>DB: live_catalog insert, status=PUBLISHED
    WF->>AU: HUMAN_REVIEW (who, when, decision)
```

## Key decisions
| Decision | Why |
|---|---|
| Classify first, then extract with the category's attribute spec | Extraction prompt asks only for the attributes that category needs; fewer hallucinated fields. |
| Two validation layers (structural vs semantic) | Structural failure = model problem -> retry. Semantic failure (missing size) = supplier data problem -> quarantine with reason code, no wasted retries. |
| Dynamic Pydantic model from `required_attributes_json` | Taxonomy owners change requirements in data, not code. |
| Canonical title built from a template, not LLM free text | Titles are consistent and can't carry injected or prohibited text from the model. |
| Guardrail BLOCK short-circuits the LLM | Injection attempts never reach the model; saves tokens. |
| Humans cannot approve quarantined listings | Compliance is a hard gate; reviewers can only reject or ask the supplier to fix. |
| `mock` provider default | Tests/evals/demos run offline and deterministically; the mock injects malformed JSON to exercise the retry path. |
| SQLite + FastAPI + Streamlit | Simplest stack meeting the requirements; same service layer serves UI and API. |
