# Estimates & Risk Register

## Estimates (120-minute time box)
| Task | Owner session | Time-box (min) | Actual (min) | Status |
|---|---|---|---|---|
| Plan, requirements, journeys, approval | main | 12 | 10 | Done |
| Interface contract + frontend brief | main | 5 | 5 | Done |
| Schemas, config, DB schema, synthetic seed | main | 12 | 10 | Done |
| Normalisers, LLM client (anthropic + mock), validated retry loop | main | 15 | 12 | Done |
| Classifier, extractor, rules engine, workflow, audit | main | 15 | 12 | Done |
| Catalog Inspection API (FastAPI) | main | 8 | 5 | Done |
| Streamlit UI (6 pages) | frontend | 40 | ~40 | Done (parallel) |
| Guardrails, observability, analytics, diagnostics | main | 15 | 10 | Done |
| pytest suite (42 tests) | main | 10 | 8 | Done |
| Golden evals (26) + runner | main | 8 | 6 | Done |
| Docs, diagrams, .docx, PPTX, GitHub push | main | 17 | 15 | Done |
| Buffer | - | 5 | - | - |
| Optional: live Anthropic eval run | main | 10 | 0 | Blocked: no API key on machine |

## Risk register
| ID | Risk | Likelihood | Impact | Mitigation | Owner | Status |
|---|---|---|---|---|---|---|
| R1 | LLM returns malformed / off-schema JSON | H | M | Pydantic `extra=forbid`, closed enums, retry with error feedback, quarantine after 3 attempts | Eng | Mitigated (tested) |
| R2 | LLM hallucinates category outside taxonomy | M | H | Enum in tool schema + Python membership check | Eng | Mitigated (tested) |
| R3 | Prohibited claim reaches live catalog | M | H | Deterministic keyword rules on raw + clean text, publish gate, human review | Compliance | Mitigated |
| R4 | Prompt injection in seller text | M | H | Pattern detection short-circuits LLM; untrusted text delimited; LLM has no decision power | Eng | Mitigated (pattern-based, residual risk) |
| R5 | Mock results overstate real accuracy | H | M | Documented; run `evals.run --provider anthropic` before go-live | Eng | Open |
| R6 | Keyword lists miss obfuscated terms ("r3plica") | M | M | Add normalisation (leetspeak), fuzzy match, periodic list review | Compliance | Open |
| R7 | Ambiguous listings misclassified | M | M | Confidence threshold 0.70 -> LOW_CONFIDENCE quarantine | Merch | Mitigated |
| R8 | API outage during demo | M | M | Automatic fallback to mock provider, recorded in run snapshot | Eng | Mitigated |
| R9 | Reviewer identity not authenticated | H | M | Out of MVP scope; add SSO/RBAC for production | Platform | Open |
| R10 | SQLite concurrency limits at scale | L (MVP) | M | WAL mode; move to Postgres for production | Platform | Accepted |
