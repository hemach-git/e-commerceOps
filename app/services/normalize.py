"""Deterministic cleaning & normalisation (no LLM). Used before and after the LLM call so that
canonical values (sizes, colours, pack counts, connector types, titles) never depend on free-form model text."""
from __future__ import annotations

import re
from typing import Iterable, Optional

SIZE_MAP = {
    "xs": "XS", "xsmall": "XS", "x-small": "XS", "extra small": "XS",
    "s": "S", "sm": "S", "sml": "S", "small": "S",
    "m": "M", "md": "M", "med": "M", "medium": "M",
    "l": "L", "lg": "L", "lrg": "L", "large": "L",
    "xl": "XL", "x-large": "XL", "xlarge": "XL", "extra large": "XL", "xlg": "XL",
    "xxl": "XXL", "2xl": "XXL", "xx-large": "XXL", "2x": "XXL",
    "xxxl": "3XL", "3xl": "3XL", "3x": "3XL",
}

COLOR_MAP = {
    "blk": "Black", "bk": "Black", "black": "Black",
    "nvy": "Navy", "navy": "Navy", "navy blue": "Navy",
    "gry": "Gray", "grey": "Gray", "gray": "Gray", "heather grey": "Heather Gray", "heather gray": "Heather Gray",
    "wht": "White", "wh": "White", "white": "White",
    "rd": "Red", "red": "Red", "blu": "Blue", "blue": "Blue", "grn": "Green", "green": "Green",
    "orng": "Orange", "org": "Orange", "orange": "Orange", "pnk": "Pink", "pink": "Pink",
    "slv": "Silver", "silver": "Silver", "khaki": "Khaki", "charcoal": "Charcoal", "teal": "Teal",
    "wine": "Wine", "sage": "Sage", "cream": "Cream", "multi": "Multicolor", "multicolor": "Multicolor",
}

CONNECTOR_MAP = {
    "usb c": "USB-C", "usb-c": "USB-C", "usbc": "USB-C", "type c": "USB-C", "type-c": "USB-C",
    "lightning": "Lightning", "micro usb": "Micro-USB", "micro-usb": "Micro-USB", "microusb": "Micro-USB",
    "usb a": "USB-A", "usb-a": "USB-A",
}

FORM_MAP = {
    "capsule": "Capsule", "capsules": "Capsule", "caps": "Capsule", "tablet": "Tablet", "tablets": "Tablet",
    "gummy": "Gummy", "gummies": "Gummy", "powder": "Powder", "softgel": "Softgel", "softgels": "Softgel",
}

SEO_TAIL = re.compile(r"\s+[-–|]\s+[A-Za-z ,&/]{3,60}$")  # "- Running Workout" style keyword tails
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]+")
PACK_RX = [
    re.compile(r"\bpack\s+of\s+(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*-?\s*(?:pack|pk|pcs?|pc|piece|count|ct)\b", re.I),
    re.compile(r"\bset\s+of\s+(\d{1,2})\b", re.I),
]
SMALL_WORDS = {"a", "an", "and", "or", "for", "of", "the", "with", "in", "on", "to", "w/"}


def clean_text(s: str) -> str:
    s = EMOJI.sub("", s or "")
    s = re.sub(r"[!]{2,}", "!", s)
    s = re.sub(r"\bw/\s*", "with ", s, flags=re.I)
    s = s.replace("&", "and")
    return re.sub(r"\s+", " ", s).strip(" -–|,.")


def is_shouting(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 10 and sum(c.isupper() for c in letters) / len(letters) > 0.8


def smart_title(s: str) -> str:
    out = []
    for i, w in enumerate(s.split()):
        lw = w.lower()
        if i > 0 and lw in SMALL_WORDS:
            out.append(lw)
        elif re.fullmatch(r"[A-Z0-9-]{2,5}", w) and any(c.isdigit() for c in w) or w in {"USB-C", "USB", "GaN", "PD"}:
            out.append(w)
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() if p else p for p in w.split("-")))
    return " ".join(out)


def strip_title_noise(raw: str) -> str:
    """Remove SEO keyword tails, parenthesised variation blobs and pack phrases from a raw title."""
    s = clean_text(raw)
    s = SEO_TAIL.sub("", s)
    s = re.sub(r"\([^)]*\)", "", s)
    for rx in PACK_RX:
        s = rx.sub("", s)
    s = re.sub(r"\bsize\s+\S+$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -–|,.")


def parse_pack_count(text: str) -> int:
    for rx in PACK_RX[:1] + PACK_RX[2:]:
        if m := rx.search(text or ""):
            return max(1, int(m.group(1)))
    m = re.search(r"\b(\d{1,2})\s*-?\s*(?:pack|pk)\b", text or "", re.I)
    return int(m.group(1)) if m else 1


def parse_piece_count(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,5})\s*-?\s*(?:pieces?|pcs?|pc|piece)\b", text or "", re.I)
    return int(m.group(1)) if m else None


def split_tokens(tag: str) -> list[str]:
    return [t.strip() for t in re.split(r"[/,;&+]|\band\b", tag or "", flags=re.I) if t.strip()]


def normalize_size(tok: str) -> Optional[str]:
    t = tok.strip().lower().replace("size", "").strip()
    if t in SIZE_MAP:
        return SIZE_MAP[t]
    m = re.fullmatch(r"(?:us\s*)?(\d{1,2}(?:\.5)?)\s*(?:m|w|us)?", t)
    if m:
        return m.group(1)
    return None


def normalize_sizes(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        for tok in split_tokens(v):
            n = normalize_size(tok)
            if n and n not in out:
                out.append(n)
    return out


def normalize_color(tok: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", tok.strip().lower())
    if not t or len(t) > 30:
        return None
    return COLOR_MAP.get(t, smart_title(t))


def normalize_colors(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        for tok in split_tokens(v):
            c = normalize_color(tok)
            if c and c not in out:
                out.append(c)
    return out


def normalize_enum(value, mapping: dict[str, str]) -> Optional[str]:
    if value is None:
        return None
    return mapping.get(str(value).strip().lower(), str(value).strip())


def parse_price(raw: str) -> Optional[float]:
    s = (raw or "").upper().replace("USD", "").replace("$", "").strip()
    if re.fullmatch(r"\d+,\d{2}", s):
        s = s.replace(",", ".")
    s = s.replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


GENDER_WORD = {"Men": "Men's", "Women": "Women's", "Kids": "Kids'", "Unisex": "Unisex"}


def build_title(brand: Optional[str], gender: Optional[str], product_type: str,
                features: list[str], pack_count: int, max_len: int = 150) -> str:
    """Canonical title template: Brand + Gender + Product Type + up to 2 features + (Pack of N)."""
    pt = smart_title(clean_text(product_type))
    pt = re.sub(r"^(men'?s?|women'?s?|womens|mens|kids'?|unisex)\s+", "", pt, flags=re.I)
    parts = []
    if brand:
        parts.append(smart_title(clean_text(brand)))
    if gender:
        parts.append(GENDER_WORD.get(gender, gender))
    parts.append(pt)
    title = " ".join(parts)
    feats = [smart_title(clean_text(f)) for f in features if f and f.lower() not in title.lower()][:2]
    if feats:
        title += ", " + ", ".join(feats)
    if pack_count and pack_count > 1:
        title += f" (Pack of {pack_count})"
    return title[:max_len].rstrip()
