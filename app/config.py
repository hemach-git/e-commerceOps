"""Config loader: config.yaml + env overrides. Secrets come from env only."""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def _base() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_config() -> dict:
    cfg = copy.deepcopy(_base())
    if os.getenv("LLM_PROVIDER"):
        cfg["llm"]["provider"] = os.getenv("LLM_PROVIDER").strip().lower()
    if os.getenv("LLM_MODEL"):
        cfg["llm"]["model"] = os.getenv("LLM_MODEL").strip()
    return cfg


def db_path() -> Path:
    p = Path(os.getenv("CATALOG_DB_PATH") or get_config()["paths"]["db"])
    return p if p.is_absolute() else ROOT / p


def path(key: str) -> Path:
    p = Path(get_config()["paths"][key])
    return p if p.is_absolute() else ROOT / p


def public_snapshot() -> dict:
    """Config safe to display/persist (no secrets)."""
    cfg = get_config()
    cfg["llm"]["api_key_configured"] = bool(os.getenv("ANTHROPIC_API_KEY"))
    return cfg
