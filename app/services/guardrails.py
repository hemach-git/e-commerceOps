"""Input guardrails: length/type limits, prompt-injection detection, PII detection & masking,
and delimiting of untrusted seller content before it is placed in an LLM prompt."""
from __future__ import annotations

import re

from app.config import get_config
from app.models.schemas import RawListing, RuleViolation

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(the\s+)?(previous|prior|above|system)",
    r"system\s*prompt",
    r"you\s+are\s+now\s+",
    r"(mark|flag|treat)\s+(this\s+)?(listing\s+)?as\s+compliant",
    r"approve\s+(this|the)\s+listing",
    r"approve\s+it\b",
    r"</?\s*(untrusted_listing|system|assistant|instructions?)\s*>",
    r"\bassistant\s*:",
    r"jailbreak",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|(?i:api[_-]?key)\s*[=:]\s*\S+)")


def detect_injection(text: str) -> list[str]:
    return [m.group(0) for p in _INJ if (m := p.search(text or ""))]


def detect_pii(text: str) -> list[str]:
    found = []
    for name, rx in (("email", EMAIL), ("phone", PHONE), ("ssn", SSN)):
        if rx.search(text or ""):
            found.append(name)
    return found


def mask_sensitive(text: str) -> str:
    if not text:
        return text
    text = SECRET.sub("[SECRET]", text)
    text = EMAIL.sub("[EMAIL]", text)
    text = SSN.sub("[SSN]", text)
    return PHONE.sub("[PHONE]", text)


def check_input(raw: RawListing) -> list[RuleViolation]:
    """Deterministic pre-LLM checks. Any BLOCK here short-circuits the LLM call."""
    cfg = get_config()["pipeline"]
    v: list[RuleViolation] = []
    if len(raw.raw_title) > cfg["max_input_title_chars"]:
        v.append(RuleViolation(code="INPUT_TOO_LONG", field="raw_title",
                               detail=f"title {len(raw.raw_title)} chars > {cfg['max_input_title_chars']}"))
    if len(raw.raw_bullets) > cfg["max_input_bullets_chars"]:
        v.append(RuleViolation(code="INPUT_TOO_LONG", field="raw_bullets",
                               detail=f"bullets {len(raw.raw_bullets)} chars > {cfg['max_input_bullets_chars']}"))
    blob = " ".join([raw.raw_title, raw.raw_bullets, raw.raw_size_tag, raw.raw_color_tag])
    hits = detect_injection(blob)
    if hits:
        v.append(RuleViolation(code="PROMPT_INJECTION_DETECTED", field="raw_title/raw_bullets",
                               detail=f"matched: {hits[0]!r}"))
    pii = detect_pii(blob)
    if pii:
        v.append(RuleViolation(code="PII_IN_LISTING", field="raw_title/raw_bullets",
                               detail=f"contains {', '.join(pii)}"))
    return v


def wrap_untrusted(raw: RawListing) -> str:
    """Delimit seller text as data. Angle brackets are neutralised so the block cannot be closed early."""
    def esc(s: str) -> str:
        return (s or "").replace("<", "‹").replace(">", "›")
    return (
        "<untrusted_listing>\n"
        f"title: {esc(raw.raw_title)}\n"
        f"bullets: {esc(raw.raw_bullets)}\n"
        f"supplier_size_tag: {esc(raw.raw_size_tag)}\n"
        f"supplier_color_tag: {esc(raw.raw_color_tag)}\n"
        "</untrusted_listing>"
    )
