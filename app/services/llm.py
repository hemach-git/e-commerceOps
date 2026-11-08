"""Configurable LLM client. Providers: `anthropic` (tool-use structured output) and `mock`
(deterministic, offline; used for tests/evals/demo fallback).

The client returns RAW model JSON text. It never decides anything: callers validate with Pydantic."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_config
from app.services import normalize as N


@dataclass
class LLMResponse:
    text: str                      # raw JSON text as produced by the model
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    meta: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        cfg = get_config()["llm"]
        self.cfg = cfg
        self.provider = (provider or cfg["provider"]).lower()
        self.model = model or cfg["model"]
        if self.provider == "mock":
            self.model = "mock-deterministic-v1"
        elif self.provider == "anthropic":
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise LLMError("ANTHROPIC_API_KEY not set; set it in .env or use LLM_PROVIDER=mock")
            import anthropic
            self._client = anthropic.Anthropic(timeout=cfg.get("timeout_s", 30), max_retries=2)
        else:
            raise LLMError(f"unknown LLM provider {self.provider!r}")

    def structured(self, *, task: str, system: str, user: str, schema: dict, attempt: int,
                   mock_ctx: Optional[dict] = None) -> LLMResponse:
        if self.provider == "mock":
            return _mock(task, attempt, mock_ctx or {})
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=self.cfg.get("max_tokens", 1024), system=system,
            messages=[{"role": "user", "content": user}],
            tools=[{"name": f"submit_{task}", "description": f"Submit the {task} result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": f"submit_{task}"},
        )
        if self.cfg.get("temperature") is not None:
            kwargs["temperature"] = self.cfg["temperature"]
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        text = json.dumps(block.input) if block is not None else "".join(
            getattr(b, "text", "") for b in resp.content)
        return LLMResponse(text=text, provider="anthropic", model=self.model,
                           tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens)


# ----------------------------------------------------------------------------------------------
# Deterministic mock "model": keyword heuristics. Deliberately imperfect:
#   * ~1 in 5 listings return malformed JSON on the first attempt (exercises retry path)
#   * returns low confidence when keywords are ambiguous / absent
# ----------------------------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "APP-SHORTS-M": ["shorts"],
    "APP-LEGGINGS-W": ["leggings", "legging", "yoga pants", "capri"],
    "APP-TEES": ["t-shirt", "tee", "t shirt", "crew neck", "v-neck"],
    "FTW-RUN": ["running shoe", "running shoes", "sneakers", "trail running", "road running"],
    "HOME-COOK": ["cookware", "frying pan", "skillet", "pots", "pans", "pan"],
    "ELEC-CHRG": ["charger", "cable", "charging", "adapter", "usb"],
    "HLTH-SUPP": ["vitamin", "capsules", "gummies", "softgels", "supplement", "protein powder", "ashwagandha"],
    "TOYS-BLD": ["building blocks", "building kit", "building block", "bricks", "stem"],
}
BRANDS = ["athletix", "peakform", "lunaflex", "kora active", "stride", "flyrun"]
FEATURES = ["dry-fit", "quick dry", "pockets", "high-waisted", "fleece lined", "waterproof", "breathable",
            "lightweight", "nonstick", "induction ready", "tri-ply", "pre-seasoned", "braided", "fast charging",
            "foldable", "carbon plate", "moisture wicking", "tummy control", "2-in-1", "liner"]
PRODUCT_NOUNS = {  # (keyword, canonical product noun) - first match wins, last entry is the default
    "APP-SHORTS-M": [("basketball", "Basketball Shorts"), ("cargo", "Cargo Shorts"), ("compression", "Compression Shorts"),
                     ("running", "Running Shorts"), ("", "Athletic Shorts")],
    "APP-LEGGINGS-W": [("capri", "Capri Leggings"), ("yoga", "Yoga Leggings"), ("fleece", "Fleece Leggings"), ("", "Leggings")],
    "APP-TEES": [("graphic", "Graphic T-Shirt"), ("v-neck", "V-Neck T-Shirt"), ("crew", "Crew Neck T-Shirt"), ("", "T-Shirt")],
    "FTW-RUN": [("trail", "Trail Running Shoes"), ("", "Running Shoes")],
    "HOME-COOK": [("frying pan", "Frying Pan"), ("skillet", "Skillet Set"), ("", "Cookware Set")],
    "ELEC-CHRG": [("wall adapter", "Wall Charger"), ("gan", "GaN Charger"), ("cable", "Charging Cable"), ("", "Charger")],
    "HLTH-SUPP": [("vitamin d3", "Vitamin D3 Softgels"), ("elderberry", "Elderberry Gummies"),
                  ("ashwagandha", "Ashwagandha Capsules"), ("protein", "Protein Powder"), ("", "Supplement")],
    "TOYS-BLD": [("castle", "Castle Building Set"), ("robot", "Robot Building Kit"), ("", "Building Set")],
}


def _product_noun(category_id, text, title):
    for kw, noun in PRODUCT_NOUNS.get(category_id or "", []):
        if kw in text:
            return noun
    return (N.strip_title_noise(title)[:60] or "Item")


def _h(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


def _approx_tokens(*parts: str) -> int:
    return sum(len(p or "") for p in parts) // 4


def _mock(task: str, attempt: int, ctx: dict) -> LLMResponse:
    raw = ctx["raw"]  # RawListing
    text = f"{raw.raw_title} {raw.raw_bullets}".lower()
    if attempt == 1 and _h(raw.supplier_sku + task) % 5 == 0:
        bad = '{"category_id": "APP-SHORTS-M", confidence: high' if task == "classify" else '{"product_type": "x", '
        return LLMResponse(text=bad, provider="mock", model="mock-deterministic-v1",
                           tokens_in=_approx_tokens(text), tokens_out=12, meta={"injected_fault": "malformed_json"})
    out = _mock_classify(text) if task == "classify" else _mock_extract(raw, text, ctx.get("category_id"))
    body = json.dumps(out)
    return LLMResponse(text=body, provider="mock", model="mock-deterministic-v1",
                       tokens_in=_approx_tokens(text) + 150, tokens_out=_approx_tokens(body))


def _kw_hit(text: str, kw: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", text) is not None


def _mock_classify(text: str) -> dict:
    scores = {cid: sum(len(kw) for kw in kws if _kw_hit(text, kw)) for cid, kws in CATEGORY_KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    (best, s1), (_, s2) = ranked[0], ranked[1]
    if s1 == 0:
        return {"category_id": "APP-TEES", "confidence": 0.2, "rationale": "No category keywords found; weak guess."}
    conf = round(min(0.98, 0.55 + 0.45 * (s1 - s2) / s1), 2)
    return {"category_id": best, "confidence": conf,
            "rationale": f"Matched keywords for {best} (score {s1} vs next {s2})."}


def _mock_extract(raw, text: str, category_id: Optional[str]) -> dict:
    title = raw.raw_title
    brand = next((b for b in BRANDS if b in text), None)
    gender = None
    if re.search(r"\bwomen'?s?\b|\bwomens\b", text):
        gender = "Women"
    elif re.search(r"\bmen'?s?\b|\bmens\b", text):
        gender = "Men"
    elif re.search(r"\bkids?\b|\bchildren\b", text):
        gender = "Kids"
    elif "unisex" in text:
        gender = "Unisex"
    paren = re.findall(r"\(([^)]*)\)", title)
    size_src = [raw.raw_size_tag] + [p for p in paren]
    m = re.search(r"\bsize\s+([A-Za-z0-9.]+)", title, re.I)
    if m:
        size_src.append(m.group(1))
    sizes = N.normalize_sizes(size_src) if category_id and category_id.startswith(("APP", "FTW")) else []
    colors = N.normalize_colors([raw.raw_color_tag]) if raw.raw_color_tag else []
    attrs: dict[str, Any] = {}
    combined = f"{title} {raw.raw_bullets}"
    if category_id == "HOME-COOK":
        mat = next((m for m in ["stainless steel", "cast iron", "ceramic", "aluminum"] if m in text), None)
        if mat:
            attrs["material"] = N.smart_title(mat)
        pc = N.parse_piece_count(combined)
        if pc is None and re.search(r"\b(pan|skillet)\b", text):
            pc = 1
        if pc is not None:
            attrs["piece_count"] = pc
    if category_id == "ELEC-CHRG":
        for k, v in N.CONNECTOR_MAP.items():
            if k in text:
                attrs["connector_type"] = v
                break
        if w := re.search(r"\b(\d{1,3})\s*w\b", text):
            attrs["wattage"] = int(w.group(1))
    if category_id == "HLTH-SUPP":
        if c := re.search(r"\b(\d{1,4})\s*(?:count|ct|gummies|softgels|capsules|tablets)\b", text):
            attrs["count"] = int(c.group(1))
        for k, v in N.FORM_MAP.items():
            if re.search(rf"\b{k}\b", text):
                attrs["form"] = v
                break
    if category_id == "TOYS-BLD":
        if pc := N.parse_piece_count(combined):
            attrs["piece_count"] = pc
        if a := re.search(r"\b(?:ages?\s*(\d{1,2})\s*\+|(\d{1,2})\s*and\s*up)", text):
            attrs["min_age"] = int(a.group(1) or a.group(2))
    material = None
    for mat in ["polyester", "cotton", "nylon", "spandex", "mesh", "fleece"]:
        if mat in text:
            material = N.smart_title(mat)
            break
    return {
        "brand": N.smart_title(brand) if brand else None,
        "product_type": _product_noun(category_id, text, title),
        "key_features": [N.smart_title(f) for f in FEATURES if f in text][:3],
        "gender": gender,
        "material": material,
        "pack_count": N.parse_pack_count(title) if category_id not in ("HOME-COOK", "TOYS-BLD", "HLTH-SUPP") else 1,
        "sizes": sizes,
        "colors": colors,
        "attributes": attrs,
    }
