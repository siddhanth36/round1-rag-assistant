"""LLM provider interface and the Groq adapter.

Configure with ``LLM_PROVIDER=groq``, ``GROQ_API_KEY`` and optionally ``GROQ_MODEL``
(default ``llama-3.3-70b-versatile``) and ``LLM_TIMEOUT_SECONDS``. Empty ``LLM_PROVIDER``
means no provider: ``create_llm_client()`` returns ``None``.

An adapter returns the model's raw text only. Parsing, source-tag resolution, citation
validation and abstention stay in ``answer_service`` / ``citation_validator``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from rag.config import LLMConfig, load_llm_config
from rag.exceptions import ConfigError, LLMProviderError


class LLMClient(Protocol):
    """What answer generation needs from an LLM provider."""

    @property
    def model_name(self) -> str: ...

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return the model's raw text reply. Raise LLMProviderError on provider failure."""
        ...


class GroqClient:
    """Groq chat completions via the official ``groq`` SDK."""

    def __init__(self, config: LLMConfig) -> None:
        if not config.api_key:
            raise ConfigError("GROQ_API_KEY must be set when LLM_PROVIDER=groq")
        from groq import Groq  # imported here so `rag` imports without the SDK when Groq is unused

        self._model = config.model
        self._api_key = config.api_key
        self._client = Groq(api_key=config.api_key, timeout=config.timeout_seconds)  # SDK retries 429/5xx twice

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        import groq

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},  # prompt already demands one JSON object
            )
        except groq.APIError as exc:
            status = getattr(exc, "status_code", None)
            detail = str(exc).replace(self._api_key, "***")  # never echo the key, even if the SDK did
            # `from None`: the chained SDK exception is not needed and could carry request details.
            raise LLMProviderError(
                f"Groq {type(exc).__name__}{f' (HTTP {status})' if status else ''}: {detail}"
            ) from None
        return response.choices[0].message.content or ""


# provider name -> factory(config) -> LLMClient
_ADAPTERS: dict[str, Callable[[LLMConfig], LLMClient]] = {"groq": GroqClient}


def create_llm_client(config: LLMConfig | None = None) -> LLMClient | None:
    """Return the configured LLM client, or ``None`` when no provider is configured.

    Raises:
        ConfigError: ``LLM_PROVIDER`` names a provider with no adapter, or required
            settings for that provider (model, API key) are missing.
    """
    config = config or load_llm_config()
    if not config.provider:
        return None
    factory = _ADAPTERS.get(config.provider)
    if factory is None:
        available = ", ".join(sorted(_ADAPTERS))
        raise ConfigError(f"No adapter for LLM_PROVIDER={config.provider!r} (available: {available})")
    if not config.model:
        raise ConfigError("An LLM model must be set (GROQ_MODEL) when LLM_PROVIDER is set")
    return factory(config)
