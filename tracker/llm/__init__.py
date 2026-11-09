"""LLM providers behind one interface, tried in a fallback chain configured in settings.yaml."""
from __future__ import annotations

import logging
import os
from typing import Dict, List

from .base import AllLLMsFailed, LLMClient, LLMError
from .chain import LLMChain

log = logging.getLogger(__name__)

DEFAULT_OPENROUTER_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "google/gemma-4-31b-it:free",
]


def openrouter_key() -> str:
    return (os.environ.get("OPENROUTER_KEY") or os.environ.get("OPENROUTER_API_KEY") or "").strip()


def _models(env_var: str, configured) -> List[str]:
    raw = os.environ.get(env_var, "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    if isinstance(configured, str):
        return [configured]
    return list(configured or [])


def build_chain(settings: Dict) -> LLMChain:
    """Build the chain from settings['llm']. Options whose key is missing are skipped with a warning."""
    cfg = settings.get("llm") or {}
    timeout = float(cfg.get("timeout_seconds", 60))
    options: List[LLMClient] = []
    for entry in cfg.get("chain") or [{"provider": "openrouter"}]:
        provider = entry.get("provider")
        if entry.get("enabled", True) is False:
            continue
        if provider == "openrouter":
            from .openrouter import OpenRouterClient

            key = openrouter_key()
            models = _models("OPENROUTER_MODELS", entry.get("models") or DEFAULT_OPENROUTER_MODELS)
            if not key:
                log.warning("OPENROUTER_KEY not set; skipping OpenRouter")
                continue
            options += [OpenRouterClient(key, m, timeout) for m in models]
        elif provider == "gemini":
            from .gemini import GeminiClient, resolve_gemini_model

            key = os.environ.get("GEMINI_API_KEY", "").strip()
            if not key:
                log.warning("GEMINI_API_KEY not set; skipping Gemini")
                continue
            models = _models("GEMINI_MODEL", entry.get("models") or [resolve_gemini_model(settings)])
            options += [GeminiClient(key, m, timeout) for m in models]
        else:
            log.warning("unknown LLM provider %r in settings; skipping", provider)
    return LLMChain(options, max_retries=int(cfg.get("max_retries", 2)),
                    backoff=float(cfg.get("backoff_seconds", 2)))


__all__ = ["AllLLMsFailed", "LLMChain", "LLMClient", "LLMError", "build_chain", "openrouter_key",
           "DEFAULT_OPENROUTER_MODELS"]
