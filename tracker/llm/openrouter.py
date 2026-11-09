"""OpenRouter client (OpenAI-compatible chat completions)."""
from __future__ import annotations

from typing import Optional

import requests

from .base import LLMClient, LLMError, status_error

BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient(LLMClient):
    provider = "openrouter"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0,
                 session: Optional[requests.Session] = None):
        super().__init__(model)
        if not api_key:
            raise ValueError("OPENROUTER_KEY is not set")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()

    def complete(self, prompt: str) -> str:
        payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4}
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Title": "Varsity AIO citation tracker"}
        try:
            # (connect, read) timeouts; non-streaming, so the read timeout bounds the whole wait
            resp = self.session.post(f"{BASE_URL}/chat/completions", json=payload, headers=headers,
                                     timeout=(10, self.timeout))
        except requests.Timeout:
            raise LLMError(f"openrouter timed out after {self.timeout:.0f}s")
        except requests.RequestException as exc:
            raise LLMError(f"openrouter request error: {exc}", retryable=True)
        if resp.status_code != 200:
            raise status_error("openrouter", resp.status_code, resp.text)
        try:
            body = resp.json()
        except ValueError:
            raise LLMError("openrouter returned non-JSON")
        if isinstance(body.get("error"), dict):  # upstream errors can arrive with HTTP 200
            err = body["error"]
            code = err.get("code")
            raise status_error("openrouter", code if isinstance(code, int) else 0, str(err.get("message", "")))
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("openrouter response had no choices")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("openrouter returned empty content")
        return content
