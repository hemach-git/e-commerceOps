"""Pydantic domain + I/O models.

Two validation layers:
1. Structural (ExtractedListing / ClassificationResult): validates the raw LLM JSON.
   Failure -> retry with error feedback -> quarantine EXTRACTION_INVALID.
2. Semantic (dynamic per-category attribute model built from
   canonical_categories.required_attributes_json): failure -> rule violations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator

ListingStatus = Literal["DRAFT", "QUARANTINED", "PUBLISHED", "REJECTED"]
Severity = Literal["BLOCK", "WARN"]
Gender = Literal["Men", "Women", "Unisex", "Kids"]
AttrValue = Union[str, int, float, list[str]]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Inputs ----------
class RawListing(BaseModel):
    """A raw third-party seller row (untrusted)."""
    model_config = ConfigDict(str_strip_whitespace=True)

    feed_id: Optional[int] = None
    supplier_id: str = Field(min_length=1, max_length=40)
    supplier_sku: str = Field(min_length=1, max_length=60)
    raw_title: str = Field(min_length=1, max_length=2000)  # hard cap; guardrails apply stricter limit
    raw_bullets: str = Field(default="", max_length=10000)
    raw_size_tag: str = Field(default="", max_length=200)
    raw_color_tag: str = Field(default="", max_length=200)
    raw_price: str = Field(default="", max_length=40)


class AttributeSpec(BaseModel):
    """One entry of canonical_categories.required_attributes_json."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z_]{2,30}$")
    type: Literal["str", "int", "enum", "enum_list", "list"]
    required: bool = True
    allowed: Optional[list[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None


# ---------- LLM structured outputs ----------
class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(min_length=2, max_length=40)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=300)


class ExtractedListing(BaseModel):
    """Structural schema for the LLM extraction call. extra='forbid' rejects hallucinated keys."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brand: Optional[str] = Field(default=None, max_length=40)
    product_type: str = Field(min_length=2, max_length=60)
    key_features: list[str] = Field(default_factory=list, max_length=5)
    gender: Optional[Gender] = None
    material: Optional[str] = Field(default=None, max_length=60)
    pack_count: int = Field(default=1, ge=1, le=50)
    sizes: list[str] = Field(default_factory=list, max_length=20)
    colors: list[str] = Field(default_factory=list, max_length=20)
    attributes: dict[str, AttrValue] = Field(default_factory=dict)

    @field_validator("key_features")
    @classmethod
    def _short_features(cls, v: list[str]) -> list[str]:
        for f in v:
            if not (1 <= len(f) <= 40):
                raise ValueError("each key_feature must be 1-40 chars")
        return v

    @field_validator("attributes")
    @classmethod
    def _attr_keys(cls, v: dict) -> dict:
        for k in v:
            if not k.replace("_", "").isalpha() or len(k) > 30:
                raise ValueError(f"invalid attribute key: {k!r}")
        return v


# ---------- Pipeline outputs ----------
class RuleViolation(BaseModel):
    code: str  # e.g. PROHIBITED_KEYWORD, MISSING_REQUIRED_ATTR
    severity: Severity = "BLOCK"
    field: Optional[str] = None
    detail: str = ""


class Variation(BaseModel):
    size: Optional[str] = None
    color: Optional[str] = None
    sku: str


class PipelineResult(BaseModel):
    listing_id: Optional[int] = None
    feed_id: int
    request_id: str
    status: Literal["DRAFT", "QUARANTINED"]
    clean_title: Optional[str] = None
    category_id: Optional[str] = None
    category_confidence: Optional[float] = None
    price: Optional[float] = None
    extracted: Optional[ExtractedListing] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    variations: list[Variation] = Field(default_factory=list)
    violations: list[RuleViolation] = Field(default_factory=list)
    extraction_attempts: int = 0
    classification_attempts: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0


class BatchSummary(BaseModel):
    run_id: str
    total: int
    drafted: int
    quarantined: int
    errors: int
    duration_ms: int
    results: list[PipelineResult] = Field(default_factory=list)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    listing_id: int = Field(ge=1)
    decision: Literal["APPROVE", "REJECT"]
    reviewer: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z0-9 ._@-]+$")
    note: str = Field(default="", max_length=500)


# ---------- Dynamic per-category attribute model ----------
def build_attribute_model(category_id: str, specs: list[AttributeSpec]) -> type[BaseModel]:
    """Build a strict Pydantic model from a category's required_attributes_json."""
    fields: dict[str, Any] = {}
    for s in specs:
        default = ... if s.required else None
        if s.type == "enum":
            tp: Any = Literal[tuple(s.allowed or [])]  # type: ignore[misc]
            fields[s.name] = (tp if s.required else Optional[tp], default)
        elif s.type == "enum_list":
            tp = list[Literal[tuple(s.allowed or [])]]  # type: ignore[misc]
            fields[s.name] = (tp if s.required else Optional[tp],
                              Field(default=default, min_length=1 if s.required else 0))
        elif s.type == "list":
            fields[s.name] = (list[str] if s.required else Optional[list[str]],
                              Field(default=default, min_length=1 if s.required else 0))
        elif s.type == "int":
            fields[s.name] = (int if s.required else Optional[int],
                              Field(default=default, ge=s.min, le=s.max, strict=False))
        else:  # str
            fields[s.name] = (str if s.required else Optional[str],
                              Field(default=default, min_length=1, max_length=80))
    safe = "Attrs_" + "".join(ch if ch.isalnum() else "_" for ch in category_id)
    return create_model(safe, __config__=ConfigDict(extra="ignore"), **fields)


__all__ = [
    "RawListing", "AttributeSpec", "ClassificationResult", "ExtractedListing", "RuleViolation",
    "Variation", "PipelineResult", "BatchSummary", "ReviewDecision", "build_attribute_model",
    "ValidationError", "utcnow", "ListingStatus",
]
