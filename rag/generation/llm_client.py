"""Provider-independent LLM interface.

No LLM provider has been approved yet (the challenge says model access is announced
separately), so no concrete adapter is implemented. To activate a provider:

1. Write a class with a ``model_name`` property and
   ``complete(*, system_prompt, user_prompt) -> str`` that returns the model's raw text.
   It must raise :class:`~rag.exceptions.LLMProviderError` (without secrets in the message)
   for any provider failure, use ``config.timeout_seconds``, and request deterministic
   output (temperature 0) where the provider supports it.
2. Register it in ``_ADAPTERS`` under a provider name.
3. Set ``LLM_PROVIDER``, ``LLM_MODEL`` and ``LLM_API_KEY`` in the environment (never in Git).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from rag.config import LLMConfig, load_llm_config
from rag.exceptions import ConfigError


class LLMClient(Protocol):
    """What answer generation needs from an LLM provider."""

    @property
    def model_name(self) -> str: ...

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return the model's raw text reply. Raise LLMProviderError on provider failure."""
        ...


# provider name -> factory(config) -> LLMClient. Empty until a provider is approved.
_ADAPTERS: dict[str, Callable[[LLMConfig], LLMClient]] = {}


def create_llm_client(config: LLMConfig | None = None) -> LLMClient | None:
    """Return the configured LLM client, or ``None`` when no provider is configured.

    Raises:
        ConfigError: ``LLM_PROVIDER`` names a provider with no adapter, or required
            settings for that provider are missing.
    """
    config = config or load_llm_config()
    if not config.provider:
        return None
    factory = _ADAPTERS.get(config.provider)
    if factory is None:
        available = ", ".join(sorted(_ADAPTERS)) or "none implemented yet"
        raise ConfigError(f"No adapter for LLM_PROVIDER={config.provider!r} (available: {available})")
    if not config.model:
        raise ConfigError("LLM_MODEL must be set when LLM_PROVIDER is set")
    return factory(config)
