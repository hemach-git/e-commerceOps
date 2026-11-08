# Architecture

## Design principle
**The LLM proposes; Python disposes.** The model is used only where language understanding is needed (picking a category from a closed list, extracting facts from messy text). Every output is parsed and validated with Pydantic, retried with the validation error on failure, and then **all policy decisions** (required attributes, prohibited terms, price, PII, injection, confidence threshold, routing, publish gate) are made by deterministic Python. The LLM is never asked "is this compliant?".

## 1. System architecture
```mermaid
flowchart TB
    subgraph USERS["Users"]
        direction LR
        U["Catalog Ops / Reviewer"]
        EXT["Integrations / Engineers"]
    end
    subgraph PRES["Presentation layer"]
        direction LR
        UI["Streamlit UI<br/>Ingest, Review, Catalog,<br/>Analytics, Diagnostics"]
        API["FastAPI<br/>Catalog Inspection API"]
    end
    WF["services/workflow.py<br/>orchestration"]
    subgraph AI["AI steps: LLM proposes"]
        direction TB
        CL["classifier.py<br/>closed-enum taxonomy"]
        EX["extractor.py<br/>strict schema"]
        SV["structured.py<br/>JSON, Pydantic, retry"]
        LLM["llm.py<br/>anthropic or mock"]
        CL --> SV
        EX --> SV
        SV --> LLM
    end
    subgraph DET["Deterministic Python: decides"]
        direction TB
        GR["guardrails.py<br/>length, injection, PII"]
        NR["normalize.py<br/>sizes, colours,<br/>packs, titles"]
        RU["rules.py<br/>attributes, compliance,<br/>routing"]
        GR ~~~ NR ~~~ RU
    end
    subgraph DATA["Data, governance, ops"]
        direction TB
        AN["analytics.py<br/>diagnostics.py"]
        DB[("SQLite<br/>FK enforced")]
        AU["audit.py<br/>audit trail"]
        OB["logging.py<br/>JSON logs"]
        AN --> DB
        DB ~~~ AU ~~~ OB
    end
    USERS --> PRES
    PRES --> WF
    WF --> AI
    WF --> DET
    WF --> DATA
```

## 2. Data flow
```mermaid
flowchart TD
    A["Supplier feed row<br/>raw, untrusted"] --> B{{"Guardrails:<br/>length, injection, PII"}}
    B -- block --> Q[("Quarantine")]
    B -- pass --> C["LLM classify<br/>closed category list"]
    C --> C1{{"Valid JSON and<br/>in taxonomy?"}}
    C1 -- "no, after 3 tries" --> Q
    C1 -- yes --> D["LLM extract<br/>strict schema"]
    D --> D1{{"Pydantic valid?"}}
    D1 -- "no, after 3 tries" --> Q
    D1 -- yes --> E["Normalise: size, colour,<br/>pack, title template"]
    E --> F["Check required<br/>attributes"]
    F --> G["Compliance rules:<br/>terms, price, confidence"]
    G --> H{{"Any BLOCK?"}}
    H -- yes --> Q
    H -- no --> DR[("Draft catalog")]
    DR --> R{{"Human review"}}
    Q --> R
    R -- "approve (drafts only)" --> L[("Live catalog")]
    R -- reject --> X[("Rejected")]
```

## 3. Process sequence
```mermaid
sequenceDiagram
    actor Ops as Catalog Ops
    participant UI as UI
    participant WF as Workflow
    participant GR as Guardrails
    participant LLM as LLM
    participant RU as Rules
    participant DB as SQLite
    participant AU as Audit
    Ops->>UI: Run batch
    UI->>WF: run_batch()
    WF->>DB: save run + config snapshot
    loop each pending listing
        WF->>GR: check input
        alt injection, PII or too long
            GR-->>WF: BLOCK (no LLM call)
        else clean
            WF->>LLM: classify
            LLM-->>WF: JSON
            Note over WF: validate, retry max 2
            WF->>LLM: extract
            LLM-->>WF: JSON
            Note over WF: validate, retry max 2
            WF->>RU: normalise + check
        end
        RU-->>WF: DRAFT or QUARANTINED
        WF->>DB: save listing
        WF->>AU: log route decision
    end
    Ops->>UI: Approve draft
    UI->>WF: review_decision()
    Note over WF: publish gate
    WF->>DB: add to live catalog
    WF->>AU: log reviewer decision
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
