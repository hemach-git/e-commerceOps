# How a listing moves through the engine (plain English)

All data is synthetic.

## From seller to shop shelf
```mermaid
flowchart TD
    A[A seller sends a messy listing] --> B{Is it safe to read?}
    B -- No --> Q[Held back, with the reason]
    B -- Yes --> C[The AI suggests a category and picks out the facts]
    C --> D{Do fixed rules accept it?}
    D -- No --> Q
    D -- Yes --> E[Draft, waiting for a person]
    E --> F{Does the reviewer approve?}
    F -- No --> G[Rejected]
    F -- Yes --> H[Live in the catalog with a marketplace SKU]
```

## What the fixed rules check
```mermaid
flowchart LR
    R[Rules] --> S[The seller's own SKU is present, well formed and not reused]
    R --> T[Every extracted fact appears in the seller's text]
    R --> U[Required details are filled in and allowed]
    R --> V[No banned words, valid price, usable title]
```

## Who decides what
| Step | Decided by |
|---|---|
| Suggesting a category and reading facts | AI |
| Accepting or holding a listing | Fixed rules |
| Publishing and creating the marketplace SKU | A named reviewer |

A listing that is held back cannot be approved. The seller has to fix it and send it again.
