"""Strict validation loop around LLM calls: parse JSON -> Pydantic -> extra deterministic checks.
On failure, re-prompt with the exact validation error (bounded retries). Never trusts raw output."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Generic, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_config
from app.services.llm import LLMClient, LLMError

T = TypeVar("T", bound=BaseModel)


@dataclass
class CallOutcome(Generic[T]):
    value: Optional[T]
    attempts: int
    tokens_in: int = 0
    tokens_out: int = 0
    raw_outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model output")
    return json.loads(t[start:end + 1])


def call_validated(client: LLMClient, *, task: str, system: str, user: str, model_cls: type[T],
                   schema: Optional[dict] = None, extra_check: Optional[Callable[[T], None]] = None,
                   mock_ctx: Optional[dict] = None) -> CallOutcome[T]:
    max_attempts = 1 + int(get_config()["llm"]["max_retries"])
    out: CallOutcome[T] = CallOutcome(value=None, attempts=0)
    prompt = user
    for attempt in range(1, max_attempts + 1):
        out.attempts = attempt
        try:
            resp = client.structured(task=task, system=system, user=prompt,
                                     schema=schema or model_cls.model_json_schema(), attempt=attempt,
                                     mock_ctx=mock_ctx)
        except LLMError as e:
            out.errors.append(f"llm_error: {e}")
            continue
        out.tokens_in += resp.tokens_in
        out.tokens_out += resp.tokens_out
        out.raw_outputs.append(resp.text[:4000])
        try:
            obj = model_cls.model_validate(_extract_json(resp.text))
            if extra_check:
                extra_check(obj)
            out.value = obj
            return out
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            msg = str(e).splitlines()[:6]
            out.errors.append(" | ".join(msg))
            prompt = (f"{user}\n\nYour previous output was REJECTED by the validator:\n{' '.join(msg)}\n"
                      "Return ONLY corrected JSON that satisfies the schema.")
    return out
