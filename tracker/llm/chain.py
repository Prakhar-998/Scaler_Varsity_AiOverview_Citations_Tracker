"""Ordered fallback across LLM models/providers."""
from __future__ import annotations

import logging
import time
from typing import Callable, List, Tuple

from .base import AllLLMsFailed, LLMClient, LLMError

log = logging.getLogger(__name__)


class LLMChain:
    """Try each option in order. Retryable errors (429/5xx/529) get up to `max_retries` retries with
    backoff on the same model; then, or on any other error (timeout, 404, auth), move on.

    An option that fails is skipped for the rest of the run, so a dead model costs at most one
    timeout. When no option is left, `complete` raises AllLLMsFailed straight away.
    """

    def __init__(self, options: List[LLMClient], max_retries: int = 2, backoff: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.options = list(options)
        self.max_retries = max_retries
        self.backoff = backoff
        self.sleep = sleep
        self.dead: List[str] = []

    @property
    def available(self) -> List[LLMClient]:
        return [o for o in self.options if o.label not in self.dead]

    def complete(self, prompt: str) -> Tuple[str, str]:
        """Return (text, label of the option that answered)."""
        for option in self.available:
            for attempt in range(self.max_retries + 1):
                try:
                    return option.complete(prompt), option.label
                except LLMError as exc:
                    if exc.retryable and attempt < self.max_retries:
                        wait = self.backoff * (2 ** attempt)
                        log.warning("%s: %s; retry %d/%d in %.0fs", option.label, exc, attempt + 1,
                                    self.max_retries, wait)
                        self.sleep(wait)
                        continue
                    log.warning("%s failed: %s; skipping it for the rest of this run", option.label, exc)
                    self.dead.append(option.label)
                    break
        raise AllLLMsFailed("all LLM options failed: " + (", ".join(self.dead) or "none configured"))
