"""Pluggable LLM Orchestrator (PRD 7.1: 'LLM-agnostic' model layer).

Engines run deterministic NLP/ML logic in-process and use the LLM layer to
enrich narratives (need-state stories, rationales, briefs). When no API key
is configured the platform degrades gracefully to template generation, so
every engine remains fully functional offline.

Per PRD 8.3 the judge model is configured separately and is never the
generation model.
"""

import json
import logging

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMClient:
    def __init__(self, model: str | None = None):
        settings = get_settings()
        self.api_key = settings.anthropic_api_key
        self.model = model or settings.anthropic_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str | None:
        """Returns model text, or None when unavailable/failed (callers fall
        back to deterministic templates)."""
        if not self.available:
            return None
        try:
            resp = httpx.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
        except Exception as exc:  # network, auth, rate limit — degrade gracefully
            logger.warning("LLM call failed, falling back to templates: %s", exc)
            return None

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict | None:
        text = self.complete(
            system + " Respond ONLY with valid JSON, no prose.", user, max_tokens
        )
        if not text:
            return None
        try:
            start, end = text.find("{"), text.rfind("}") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.warning("LLM returned non-JSON output, falling back to templates")
            return None


def generation_client() -> LLMClient:
    return LLMClient(get_settings().anthropic_model)


def judge_client() -> LLMClient:
    """Separate judge model (PRD 8.3: judge cannot be the generation model)."""
    return LLMClient(get_settings().judge_model)
