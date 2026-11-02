"""GroqClient tests with the official SDK's ``Groq`` class mocked — no network calls."""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace

import groq
import httpx
import pytest

from rag.config import DEFAULT_GROQ_MODEL, LLMConfig, load_env_file, load_llm_config
from rag.exceptions import ConfigError, LLMProviderError
from rag.generation.answer_service import answer_question
from rag.generation.llm_client import GroqClient, create_llm_client
from rag.schemas import AnswerStatus
from rag.tests.support import StubStore, search_result

KEY = "gsk_test_not_a_real_key_123456"
URL = "https://api.groq.com/openai/v1/chat/completions"


class FakeGroq:
    """Stands in for groq.Groq: records constructor and create() kwargs, returns or raises ``reply``."""

    instances: list[FakeGroq] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs, self.calls, self.reply = kwargs, [], ""
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        FakeGroq.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


@pytest.fixture()
def fake_groq(monkeypatch: pytest.MonkeyPatch) -> type[FakeGroq]:
    FakeGroq.instances = []
    monkeypatch.setattr(groq, "Groq", FakeGroq)
    return FakeGroq


def _client(**overrides) -> GroqClient:
    return GroqClient(LLMConfig(**{"provider": "groq", "model": DEFAULT_GROQ_MODEL, "api_key": KEY, **overrides}))


# --- configuration -------------------------------------------------------------------------------


def test_env_config_and_factory_build_groq_client(monkeypatch, fake_groq) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12")
    client = create_llm_client()
    assert isinstance(client, GroqClient)
    assert client.model_name == DEFAULT_GROQ_MODEL == "llama-3.3-70b-versatile"
    assert fake_groq.instances[0].kwargs == {"api_key": KEY, "timeout": 12}


def test_model_is_configurable(monkeypatch, fake_groq) -> None:
    monkeypatch.setenv("GROQ_MODEL", "llama-3.1-8b-instant")
    assert load_llm_config().model == "llama-3.1-8b-instant"


def test_missing_api_key_is_a_clear_config_error(fake_groq) -> None:
    with pytest.raises(ConfigError, match="GROQ_API_KEY must be set"):
        create_llm_client(LLMConfig(provider="groq", model=DEFAULT_GROQ_MODEL))
    assert fake_groq.instances == []


def test_unsupported_provider_lists_groq() -> None:
    with pytest.raises(ConfigError, match=r"No adapter for LLM_PROVIDER='openai' \(available: groq\)"):
        create_llm_client(LLMConfig(provider="openai", model="m", api_key=KEY))


def test_key_not_in_client_or_config_repr(fake_groq) -> None:
    config = LLMConfig(provider="groq", model=DEFAULT_GROQ_MODEL, api_key=KEY)
    assert KEY not in repr(config) and KEY not in repr(_client())


# --- requests ----------------------------------------------------------------------------------


def test_complete_sends_prompts_deterministically_and_returns_text(fake_groq) -> None:
    client = _client()
    fake_groq.instances[0].reply = '{"status": "insufficient_evidence", "statements": []}'
    assert client.complete(system_prompt="SYS", user_prompt="USER") == fake_groq.instances[0].reply
    assert fake_groq.instances[0].calls == [{
        "model": DEFAULT_GROQ_MODEL,
        "messages": [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }]


def test_empty_content_returns_empty_string(fake_groq) -> None:
    client = _client()
    fake_groq.instances[0].reply = None
    assert client.complete(system_prompt="s", user_prompt="u") == ""


def _status_error(cls, code: int, message: str):
    return cls(message, response=httpx.Response(code, request=httpx.Request("POST", URL)), body=None)


@pytest.mark.parametrize(
    "error, expected",
    [
        (_status_error(groq.AuthenticationError, 401, "Invalid API Key"), "Groq AuthenticationError (HTTP 401)"),
        (_status_error(groq.RateLimitError, 429, "Rate limit reached"), "Groq RateLimitError (HTTP 429)"),
        (_status_error(groq.NotFoundError, 404, "model not found"), "Groq NotFoundError (HTTP 404)"),
        (groq.APITimeoutError(request=httpx.Request("POST", URL)), "Groq APITimeoutError"),
        (groq.APIConnectionError(request=httpx.Request("POST", URL)), "Groq APIConnectionError"),
    ],
)
def test_api_failures_become_provider_errors(fake_groq, error, expected) -> None:
    client = _client()
    fake_groq.instances[0].reply = error
    with pytest.raises(LLMProviderError, match=re.escape(expected)) as info:
        client.complete(system_prompt="s", user_prompt="u")
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_api_key_is_redacted_from_error_messages(fake_groq) -> None:
    client = _client()
    fake_groq.instances[0].reply = _status_error(groq.AuthenticationError, 401, f"bad key {KEY}")
    with pytest.raises(LLMProviderError) as info:
        client.complete(system_prompt="s", user_prompt="u")
    assert KEY not in str(info.value) and "***" in str(info.value)


# --- through answer_question: validation and abstention unchanged ---------------------------------


def _hp_store(manual_chunks) -> StubStore:
    chunk = next(c for c in manual_chunks["HP-OPS-01"] if c.chunk_id == "HP-OPS-01:1.0:p1:c0")
    return StubStore([search_result(chunk, 0.4)])


def test_groq_answer_citations_come_from_source_map(fake_groq, manual_chunks) -> None:
    client = _client()
    fake_groq.instances[0].reply = json.dumps({"status": "answered", "statements": [
        {"text": "Normal hydraulic pressure is 120 to 140 bar during the forming stage.", "sources": ["S1"],
         "document_id": "FAKE-DOC-99", "pdf_page": 42, "revision": "9.9"},
    ]})
    result = answer_question("What is the normal pressure?", "M-HP200", store=_hp_store(manual_chunks), llm=client)
    assert result.status is AnswerStatus.ANSWERED and result.llm_model == DEFAULT_GROQ_MODEL
    (c,) = result.citations
    assert (c.document_id, c.revision, c.pdf_page) == ("HP-OPS-01", "1.0", 1)


def test_groq_unsupported_answer_is_rejected(fake_groq, manual_chunks) -> None:
    client = _client()
    fake_groq.instances[0].reply = json.dumps({"status": "answered", "statements": [
        {"text": "Normal pressure is 300 bar.", "sources": ["S7"]}]})
    result = answer_question("What is the normal pressure?", "M-HP200", store=_hp_store(manual_chunks), llm=client)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE and result.citations == ()
    assert len(fake_groq.instances[0].calls) == 2  # original + one correction round


def test_groq_api_failure_is_generation_error_without_key(fake_groq, manual_chunks) -> None:
    client = _client()
    fake_groq.instances[0].reply = _status_error(groq.AuthenticationError, 401, f"Invalid API Key {KEY}")
    result = answer_question("What is the normal pressure?", "M-HP200", store=_hp_store(manual_chunks), llm=client)
    assert result.status is AnswerStatus.GENERATION_ERROR
    assert result.error.startswith("LLMProviderError: Groq AuthenticationError (HTTP 401)")
    assert KEY not in repr(result)


# --- local .env loading (configuration system) ---------------------------------------------------


@pytest.fixture()
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A private copy of os.environ without LLM settings, so .env tests never touch the real one."""
    env = {k: v for k, v in os.environ.items() if k not in ("LLM_PROVIDER", "GROQ_API_KEY", "GROQ_MODEL")}
    monkeypatch.setattr(os, "environ", env)
    return env


def test_dotenv_configures_groq_and_answer_question_uses_it(tmp_path, isolated_env, fake_groq, manual_chunks) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"LLM_PROVIDER=groq\nGROQ_API_KEY={KEY}\nGROQ_MODEL=llama-3.1-8b-instant\n", encoding="utf-8")
    assert load_env_file(env_file) is True

    client = create_llm_client()                              # 1. provider recognized, 4. Groq client returned
    assert isinstance(client, GroqClient)
    assert fake_groq.instances[0].kwargs["api_key"] == KEY    # 2. key loaded from .env ...
    assert KEY not in repr(load_llm_config()) and KEY not in repr(client)  # ... and never in repr
    assert client.model_name == "llama-3.1-8b-instant"        # 3. model configurable

    fake_groq.instances[0].reply = json.dumps({"status": "answered", "statements": [
        {"text": "Normal hydraulic pressure is 120 to 140 bar during the forming stage.", "sources": ["S1"]}]})
    result = answer_question("What is the normal pressure?", "M-HP200", store=_hp_store(manual_chunks), llm=client)
    call = fake_groq.instances[0].calls[0]                    # 5. answer_question invoked the configured client
    assert call["model"] == "llama-3.1-8b-instant" and "Selected machine: M-HP200" in call["messages"][1]["content"]
    assert result.status is AnswerStatus.ANSWERED and result.llm_model == "llama-3.1-8b-instant"


def test_real_environment_wins_over_dotenv(tmp_path, isolated_env) -> None:
    isolated_env["GROQ_MODEL"] = "from-environment"
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_MODEL=from-dotenv\nGROQ_API_KEY=abc$def\n", encoding="utf-8")
    load_env_file(env_file)
    assert load_llm_config().model == "from-environment"
    assert isolated_env["GROQ_API_KEY"] == "abc$def"  # no $-interpolation of secrets


def test_missing_dotenv_is_a_no_op(tmp_path, isolated_env) -> None:
    assert load_env_file(tmp_path / "absent.env") is False
    assert create_llm_client() is None  # no provider configured -> provider_unavailable path
