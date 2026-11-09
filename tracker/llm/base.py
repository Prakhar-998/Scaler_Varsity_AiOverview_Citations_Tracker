"""Provider-neutral LLM client interface and errors."""
from __future__ import annotations

RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    """One failed call. `retryable` errors (429/5xx/529) are retried on the same model;
    anything else (timeout, auth, 404, bad response) moves straight to the next option."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class AllLLMsFailed(RuntimeError):
    """Every option in the chain has failed or is unavailable."""


class LLMClient:
    provider = "base"

    def __init__(self, model: str):
        self.model = model

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def complete(self, prompt: str) -> str:
        raise NotImplementedError


def status_error(provider: str, status: int, detail: str) -> LLMError:
    return LLMError(f"{provider} HTTP {status}: {detail[:200]}", retryable=status in RETRYABLE_STATUS)
