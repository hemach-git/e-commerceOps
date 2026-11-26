"""Deterministic policy engine. The LLM is NEVER asked whether a listing is compliant.
Every rule returns machine-readable reason codes with evidence."""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import ValidationError

from app.config import get_config
from app.models.schemas import AttributeSpec, ExtractedListing, RuleViolation, build_attribute_model
from app.services import normalize as N

REASON_CODES = {
    "PROMPT_INJECTION_DETECTED": "Seller text tries to instruct the AI system",
    "PII_IN_LISTING": "Listing contains personal contact data",
    "INPUT_TOO_LONG": "Input exceeds length limits",
    "EXTRACTION_INVALID": "LLM output failed schema validation after retries",
    "CLASSIFICATION_INVALID": "LLM classification failed validation after retries",
    "LOW_CONFIDENCE": "Category confidence below threshold",
    "PROHIBITED_KEYWORD": "Contains a prohibited term (global or category policy)",
    "MISSING_REQUIRED_ATTR": "A category-required attribute is missing",
    "INVALID_ATTR_VALUE": "An attribute value is outside the allowed values/range",
    "INVALID_PRICE": "Price missing, non-positive or above cap",
    "TITLE_TOO_SHORT": "Clean title too short to be meaningful",
    "TITLE_TRUNCATED": "Clean title exceeded max length and was truncated",
    "SHOUTING_TITLE": "Raw title was mostly upper-case (auto-fixed)",
    "MISSING_SELLER_SKU": "The seller sent no SKU",
    "INVALID_SELLER_SKU": "Seller SKU must be 2-40 letters, digits, . _ -",
    "DUPLICATE_SELLER_SKU": "This supplier already used the seller SKU",
    "UNGROUNDED_ATTR": "An extracted value does not appear in the seller's text",
}

SELLER_SKU_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{1,39}")


def check_seller_sku(stored_sku: str, declared_sku: Optional[str]) -> list[RuleViolation]:
    """`declared_sku` is set only when the stored key differs from what the seller sent."""
    sent = stored_sku if declared_sku is None else declared_sku
    if not sent.strip():
        code, detail = "MISSING_SELLER_SKU", "no seller SKU supplied"
    elif not SELLER_SKU_RX.fullmatch(sent):
        code, detail = "INVALID_SELLER_SKU", f"{sent!r}"
    elif sent != stored_sku:
        code, detail = "DUPLICATE_SELLER_SKU", f"{sent!r} was already used by this supplier"
    else:
        return []
    return [RuleViolation(code=code, field="supplier_sku", detail=detail)]


GENDER_WORDS = {"Men": ["men", "mens", "men's"], "Women": ["women", "womens", "women's"],
                "Unisex": ["unisex"], "Kids": ["kid", "kids", "kids'", "children", "boys", "girls"]}
VALUE_ALIASES = {"size": N.SIZE_MAP, "colors": N.COLOR_MAP, "connector_type": N.CONNECTOR_MAP, "form": N.FORM_MAP}


def _in_text(term: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text) is not None


def _grounded(name: str, value: Any, text: str) -> bool:
    if isinstance(value, (int, float)):
        if name == "piece_count" and value == 1:  # a single item rarely states its count
            return True
        number = int(value) if float(value).is_integer() else value
        return re.search(rf"(?<![\d.]){number}(?!\.?\d)", text) is not None
    value = str(value)
    if name == "gender":
        return any(_in_text(w, text) for w in GENDER_WORDS.get(value, [value]))
    aliases = [a for a, canon in VALUE_ALIASES.get(name, {}).items() if canon == value]
    if any(_in_text(term, text) for term in [value, *aliases]):
        return True
    if name == "size" and value.replace(".", "").isdigit():
        return re.search(rf"(?<![\d.]){re.escape(value)}(?!\.?\d)", text) is not None
    words = re.findall(r"[a-z0-9]+", value.lower())
    return bool(words) and all(_in_text(w, text) for w in words)


def check_grounding(attrs: dict[str, Any], source_text: str) -> list[RuleViolation]:
    """Anti-hallucination: every attribute value must be traceable to the seller's own text."""
    text = source_text.lower().replace("\u2019", "'")
    out = []
    for name, value in attrs.items():
        missing = [v for v in (value if isinstance(value, list) else [value]) if not _grounded(name, v, text)]
        if missing:
            out.append(RuleViolation(code="UNGROUNDED_ATTR", field=name,
                                     detail=f"{name}: {', '.join(map(str, missing))} not found in the listing"))
    return out


def _term_rx(term: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.I)


def check_prohibited(texts: dict[str, str], category: Optional[dict]) -> list[RuleViolation]:
    out: list[RuleViolation] = []
    terms = [(t, "global") for t in get_config()["compliance"]["global_prohibited_keywords"]]
    if category:
        terms += [(t, category["category_id"]) for t in category.get("prohibited_keywords", [])]
    seen = set()
    for term, scope in terms:
        for field, text in texts.items():
            if term.lower() not in seen and _term_rx(term).search(text or ""):
                seen.add(term.lower())
                out.append(RuleViolation(code="PROHIBITED_KEYWORD", field=field,
                                         detail=f"'{term}' ({scope} policy)"))
    return out


def assemble_attributes(ext: ExtractedListing, category: dict) -> dict[str, Any]:
    """Merge LLM fields into the category's attribute namespace, then deterministically normalise values."""
    attrs: dict[str, Any] = dict(ext.attributes)
    attrs["size"] = N.normalize_sizes(ext.sizes)
    attrs["colors"] = N.normalize_colors(ext.colors)
    attrs["gender"] = ext.gender
    attrs["material"] = N.smart_title(ext.material) if ext.material else attrs.get("material")
    if "connector_type" in attrs:
        attrs["connector_type"] = N.normalize_enum(attrs["connector_type"], N.CONNECTOR_MAP)
    if "form" in attrs:
        attrs["form"] = N.normalize_enum(attrs["form"], N.FORM_MAP)
    names = {s["name"] for s in category.get("required_attributes", [])}
    clean = {k: v for k, v in attrs.items() if k in names and v not in (None, [], "")}
    return clean


def validate_attributes(attrs: dict[str, Any], category: dict) -> list[RuleViolation]:
    specs = [AttributeSpec.model_validate(s) for s in category.get("required_attributes", [])]
    model = build_attribute_model(category["category_id"], specs)
    try:
        model.model_validate(attrs)
        return []
    except ValidationError as e:
        out = []
        for err in e.errors():
            name = str(err["loc"][0]) if err["loc"] else "?"
            if err["type"] == "missing":
                out.append(RuleViolation(code="MISSING_REQUIRED_ATTR", field=name,
                                         detail=f"'{name}' is required for {category['name']}"))
            else:
                if any(o.field == name for o in out):
                    continue
                out.append(RuleViolation(code="INVALID_ATTR_VALUE", field=name,
                                         detail=f"{name}={attrs.get(name)!r}: {err['msg']}"))
        return out


def check_price(price: Optional[float]) -> list[RuleViolation]:
    cap = get_config()["pipeline"]["max_price"]
    if price is None or price <= 0 or price > cap:
        return [RuleViolation(code="INVALID_PRICE", field="raw_price", detail=f"parsed price={price} (must be 0 < p <= {cap})")]
    return []


def check_title(clean_title: str, raw_title: str, truncated: bool) -> list[RuleViolation]:
    cfg = get_config()["pipeline"]
    out = []
    if len(clean_title or "") < cfg["min_title_len"]:
        out.append(RuleViolation(code="TITLE_TOO_SHORT", field="clean_title", detail=f"{clean_title!r}"))
    if truncated:
        out.append(RuleViolation(code="TITLE_TRUNCATED", severity="WARN", field="clean_title",
                                 detail=f"truncated to {cfg['max_title_len']} chars"))
    if N.is_shouting(raw_title):
        out.append(RuleViolation(code="SHOUTING_TITLE", severity="WARN", field="raw_title", detail="auto-normalised casing"))
    return out


def check_confidence(conf: float) -> list[RuleViolation]:
    th = get_config()["pipeline"]["confidence_threshold"]
    if conf < th:
        return [RuleViolation(code="LOW_CONFIDENCE", field="category_id", detail=f"confidence {conf:.2f} < {th:.2f}")]
    return []


def route(violations: list[RuleViolation]) -> str:
    return "QUARANTINED" if any(v.severity == "BLOCK" for v in violations) else "DRAFT"
