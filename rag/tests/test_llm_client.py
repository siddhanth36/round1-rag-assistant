"""Tests for the provider-independent LLM factory and LLM/answer configuration."""

from __future__ import annotations

import pytest

from rag.config import AnswerConfig, LLMConfig, load_answer_config, load_llm_config
from rag.exceptions import ConfigError
from rag.generation import llm_client
from rag.generation.llm_client import create_llm_client


def test_no_provider_configured_returns_none() -> None:
    assert create_llm_client(LLMConfig()) is None


def test_unknown_provider_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="No adapter for LLM_PROVIDER='someprovider'"):
        create_llm_client(LLMConfig(provider="someprovider", model="m"))


def test_registered_adapter_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    created = []

    class Adapter:
        def __init__(self, config: LLMConfig) -> None:
            created.append(config)

        model_name = "x"

        def complete(self, *, system_prompt: str, user_prompt: str) -> str:
            return ""

    monkeypatch.setitem(llm_client._ADAPTERS, "test", Adapter)
    client = create_llm_client(LLMConfig(provider="test", model="m", api_key="k"))
    assert isinstance(client, Adapter) and created[0].model == "m"


def test_provider_without_model_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(llm_client._ADAPTERS, "test", lambda config: object())
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        create_llm_client(LLMConfig(provider="test"))


def test_api_key_is_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", " SomeProvider ")
    monkeypatch.setenv("LLM_MODEL", "model-x")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-not-a-real-key")
    config = load_llm_config()
    assert (config.provider, config.model, config.api_key) == ("someprovider", "model-x", "sk-test-not-a-real-key")
    assert "sk-test" not in repr(config)


def test_unset_environment_means_no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert create_llm_client() is None


def test_answer_config_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_TOP_K", "6")
    monkeypatch.setenv("RAG_MAX_DISTANCE", "0.7")
    monkeypatch.setenv("RAG_MAX_CORRECTIONS", "0")
    assert load_answer_config() == AnswerConfig(retrieval_limit=6, max_distance=0.7, max_corrections=0)
    monkeypatch.setenv("RAG_MAX_DISTANCE", "off")
    assert load_answer_config().max_distance is None
    monkeypatch.setenv("RAG_MAX_DISTANCE", "high")
    with pytest.raises(ConfigError):
        load_answer_config()


@pytest.mark.parametrize(
    "kwargs", [{"retrieval_limit": 0}, {"retrieval_limit": 51}, {"max_distance": 0.0}, {"max_distance": 2.5},
               {"max_corrections": -1}, {"max_question_chars": 0}],
)
def test_invalid_answer_config_rejected(kwargs) -> None:
    with pytest.raises(ConfigError):
        AnswerConfig(**kwargs)
