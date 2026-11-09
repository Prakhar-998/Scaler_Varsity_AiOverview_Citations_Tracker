"""Gemini client (google-genai SDK). Off by default in the chain; see settings.yaml."""
from __future__ import annotations

import os
from typing import Dict

from .base import RETRYABLE_STATUS, LLMClient, LLMError

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


def resolve_gemini_model(settings: Dict) -> str:
    """GEMINI_MODEL env var, then gemini_model in settings.yaml, then the default."""
    return os.environ.get("GEMINI_MODEL", "").strip() or settings.get("gemini_model") or DEFAULT_GEMINI_MODEL


class GeminiModelNotFound(LLMError):
    """The configured Gemini model returned 404 (retired, misspelled, or not enabled for this key)."""

    def __init__(self, model: str, detail: str = ""):
        msg = f"Gemini model '{model}' returned 404 (not found or no longer available)."
        detail = detail.strip()
        if detail:
            msg += f" Gemini said: {detail}" + ("" if detail.endswith((".", "!", "?")) else ".")
        msg += " Set GEMINI_MODEL in .env, or gemini_model in config/settings.yaml, to an available model."
        super().__init__(msg, retryable=False)
        self.model = model


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0):
        super().__init__(model)
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        from google import genai  # lazy: offline tests and OpenRouter-only runs don't need it

        self.client = genai.Client(api_key=api_key, http_options={"timeout": int(timeout * 1000)})
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        from google.genai import errors

        try:
            resp = self.client.models.generate_content(
                model=self.model, contents=prompt, config={"response_mime_type": "application/json"})
        except errors.APIError as exc:
            if exc.code == 404:
                raise GeminiModelNotFound(self.model, exc.message or "") from exc
            raise LLMError(f"gemini HTTP {exc.code}: {exc.message}", retryable=exc.code in RETRYABLE_STATUS) from exc
        except Exception as exc:  # httpx timeouts / connection errors surface as various types
            raise LLMError(f"gemini call failed: {type(exc).__name__}: {exc}") from exc
        if not (resp.text or "").strip():
            raise LLMError("gemini returned empty content")
        return resp.text
