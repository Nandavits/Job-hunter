"""One place that creates the Claude client.

An Anthropic key (sk-ant-...) talks to Anthropic directly. An OpenRouter key (sk-or-...) is sent to
OpenRouter's Anthropic-compatible endpoint, and model names are translated to OpenRouter's form
(claude-sonnet-5-5 -> anthropic/claude-sonnet-5.5). Everything else in Job Hunter stays the same.
"""
from __future__ import annotations

import re

OPENROUTER_BASE = "https://openrouter.ai/api"


def is_openrouter(key: str) -> bool:
    return (key or "").strip().startswith("sk-or-")


def openrouter_model(model: str) -> str:
    """claude-sonnet-5-5 -> anthropic/claude-sonnet-5.5 (names already in OpenRouter form are kept)."""
    if "/" in model:
        return model
    return "anthropic/" + re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", model)


class _Messages:
    def __init__(self, inner):
        self._inner = inner

    def create(self, **kwargs):
        if "model" in kwargs:
            kwargs["model"] = openrouter_model(kwargs["model"])
        return self._inner.create(**kwargs)


class _OpenRouterClient:
    def __init__(self, client):
        self._client = client
        self.messages = _Messages(client.messages)


def make_client(api_key: str):
    import anthropic
    key = (api_key or "").strip()
    if is_openrouter(key):
        return _OpenRouterClient(anthropic.Anthropic(base_url=OPENROUTER_BASE, auth_token=key))
    return anthropic.Anthropic(api_key=key)
