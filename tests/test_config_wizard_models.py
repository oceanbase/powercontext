# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""User-facing model configuration choices produce isolated workload settings."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from powercontext.cli.config_wizard_models import collect_models
from powercontext.cli.config_wizard_ui import WizardUI

PREFIX = "POWERCONTEXT_SERVER_INFERENCE_"


class Answers(WizardUI):
    """A small terminal stand-in that records visible prompts, never secret answers."""

    def __init__(self, *answers: str | bool | int) -> None:
        super().__init__("en")
        self.answers = iter(answers)
        self.prompts: list[str] = []
        self.choose_prompts: list[str] = []
        self.search_prompts: list[str] = []

    def text(self, en: str, zh: str) -> str:
        return en

    def say(self, en: str, zh: str) -> None:
        self.prompts.append(en)

    section = say

    def choose(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        self.prompts.append(en)
        self.choose_prompts.append(en)
        answer = next(self.answers)
        assert isinstance(answer, str) and answer in {choice[0] for choice in choices}
        return answer

    def search(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        self.prompts.append(en)
        self.search_prompts.append(en)
        answer = next(self.answers)
        assert isinstance(answer, str) and answer in {choice[0] for choice in choices}
        return answer

    def ask(self, en: str, zh: str, default: str = "", secret: bool = False, required: bool = False) -> str:
        self.prompts.append(en)
        answer = next(self.answers)
        assert isinstance(answer, str)
        if "API key" in en:
            assert secret
        return answer or default

    def confirm(self, en: str, zh: str, default: bool = True) -> bool:
        self.prompts.append(en)
        answer = next(self.answers)
        assert isinstance(answer, bool)
        return answer

    def integer(self, en: str, zh: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
        self.prompts.append(en)
        answer = next(self.answers)
        assert isinstance(answer, int) and answer >= minimum
        return answer


def test_generation_only_does_not_ask_for_embeddings_or_modify_shared_provider_variables() -> None:
    values = {"OPENAI_API_KEY": "other-workload-key", "OPENAI_BASE_URL": "https://other.example/v1"}
    secret = 'key-with-"quotes"-and-$dollars'  # noqa: S105 -- Deliberate serialization fixture.
    ui = Answers("bailian", "", "qwen-plus", secret)

    updates = collect_models(ui, values, generation=True, embedding=False)

    assert updates[PREFIX + "GENERATION_MODEL"] == "openai-chat:qwen-plus"
    assert updates[PREFIX + "GENERATION_BASE_URL"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert json.loads(updates[PREFIX + "GENERATION_HEADERS"] or "") == {"Authorization": "Bearer " + secret}
    assert all("EMBEDDING" not in key and not key.startswith("OPENAI_") for key in updates)
    assert all("Embedding" not in prompt and secret not in prompt for prompt in ui.prompts)
    assert values["OPENAI_API_KEY"] == "other-workload-key"


def test_provider_selection_uses_searchable_menu() -> None:
    ui = Answers("bailian", "", "qwen-plus", "key")

    collect_models(ui, {}, generation=True, embedding=False)

    assert ui.search_prompts == ["Generation service"]
    assert "Generation service" not in ui.choose_prompts


def test_embedding_only_asks_for_a_profile_without_creating_a_generation_model() -> None:
    ui = Answers("openai-chat", "", "text-embedding-3-small", "embedding-key")

    updates = collect_models(ui, {}, generation=False, embedding=True)

    assert updates[PREFIX + "EMBEDDING_MODEL"] == "openai:text-embedding-3-small"
    assert updates[PREFIX + "EMBEDDING_DIMENSION"] == "1536"
    assert updates[PREFIX + "EMBEDDING_PROFILE_ID"] == "openai-text-embedding-3-small-1536-unit-v1"
    assert updates[PREFIX + "EMBEDDING_NORMALIZATION"] == "unit"
    assert all("GENERATION" not in key for key in updates)
    assert "Embedding dimensions" not in ui.prompts
    assert "Embedding profile ID" not in ui.prompts


def test_shared_bailian_connection_still_collects_a_distinct_embedding_model_and_profile() -> None:
    ui = Answers("bailian", "", "qwen-plus", "shared-key", True, "text-embedding-v4")

    updates = collect_models(ui, {}, generation=True, embedding=True)

    assert updates[PREFIX + "GENERATION_MODEL"] == "openai-chat:qwen-plus"
    assert updates[PREFIX + "EMBEDDING_MODEL"] == "openai:text-embedding-v4"
    assert updates[PREFIX + "GENERATION_HEADERS"] == updates[PREFIX + "EMBEDDING_HEADERS"]
    assert updates[PREFIX + "GENERATION_BASE_URL"] == updates[PREFIX + "EMBEDDING_BASE_URL"]
    assert (
        "Reuse the Generation API address and API key for Embedding? The Embedding model is configured separately."
    ) in ui.prompts


def test_unknown_embedding_model_explains_and_collects_dimension_without_profile_jargon() -> None:
    ui = Answers("custom", "openai-chat", "https://models.example/v1", "custom-vector", "key", 768)

    updates = collect_models(ui, {}, generation=False, embedding=True)

    assert updates[PREFIX + "EMBEDDING_DIMENSION"] == "768"
    assert updates[PREFIX + "EMBEDDING_PROFILE_ID"] == "openai-custom-vector-768-unit-v1"
    assert "Embedding dimensions" in ui.prompts
    assert "Embedding profile ID" not in ui.prompts
    assert any("stored vectors" in prompt for prompt in ui.prompts)


def test_keep_existing_native_providers_preserves_advanced_configuration() -> None:
    values = {
        PREFIX + "GENERATION_MODEL": "bedrock:anthropic.claude-sonnet",
        PREFIX + "GENERATION_MODEL_SETTINGS": '{"temperature":0.2}',
        PREFIX + "EMBEDDING_MODEL": "voyage:voyage-3",
        PREFIX + "EMBEDDING_PROFILE_ID": "existing-profile",
        PREFIX + "EMBEDDING_DIMENSION": "1024",
        PREFIX + "EMBEDDING_NORMALIZATION": "none",
        "AWS_PROFILE": "development",
        "VOYAGE_API_KEY": "existing-key",
    }
    original = dict(values)

    assert collect_models(Answers(True, True), values, generation=True, embedding=True) == {}
    assert values == original


def test_reconfigure_clears_old_workload_settings_and_replaces_credentials() -> None:
    values = {
        PREFIX + "GENERATION_MODEL": "anthropic:old-model",
        PREFIX + "GENERATION_BASE_URL": "https://old.example",
        PREFIX + "GENERATION_HEADERS": '{"x-api-key":"old-key","legacy-header":"old"}',
        PREFIX + "GENERATION_MODEL_SETTINGS": '{"anthropic_thinking":{"type":"enabled"}}',
    }
    ui = Answers(False, "openai-responses", "", "gpt-4.1-mini", "new-key")

    updates = collect_models(ui, values, generation=True, embedding=False)

    assert updates[PREFIX + "GENERATION_MODEL"] == "openai:gpt-4.1-mini"
    assert updates[PREFIX + "GENERATION_MODEL_SETTINGS"] is None
    assert json.loads(updates[PREFIX + "GENERATION_HEADERS"] or "") == {"Authorization": "Bearer new-key"}
    assert any("previous" in prompt and "settings" in prompt for prompt in ui.prompts)


def test_anthropic_uses_runtime_supported_api_key_header() -> None:
    ui = Answers("anthropic", "", "claude-sonnet-4-5", "anthropic-key")

    updates = collect_models(ui, {}, generation=True, embedding=False)

    assert updates[PREFIX + "GENERATION_MODEL"] == "anthropic:claude-sonnet-4-5"
    assert json.loads(updates[PREFIX + "GENERATION_HEADERS"] or "") == {"x-api-key": "anthropic-key"}


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://user:secret@example.com/v1",
        "https://example.com/v1?api_key=secret",
        "https://example.com/#fragment",
        "file:///tmp/models",
    ],
)
def test_invalid_endpoint_is_reprompted_without_printing_the_unsafe_value(invalid_url: str) -> None:
    ui = Answers("openai-chat", invalid_url, "https://safe.example/v1", "model", "key")

    updates = collect_models(ui, {}, generation=True, embedding=False)

    assert updates[PREFIX + "GENERATION_BASE_URL"] == "https://safe.example/v1"
    assert all(invalid_url not in prompt for prompt in ui.prompts)


def test_reranking_can_inherit_generation_and_removes_stale_rerank_overrides() -> None:
    values = {
        PREFIX + "GENERATION_MODEL": "openai-chat:kept-model",
        PREFIX + "RERANK_HEADERS": '{"x-api-key":"old-key"}',
        PREFIX + "RERANK_MODEL_SETTINGS": '{"temperature":0.5}',
    }

    ui = Answers(True, True)
    updates = collect_models(ui, values, generation=True, embedding=False, rerank=True)

    assert updates == {
        PREFIX + "RERANK_HEADERS": None,
        PREFIX + "RERANK_MODEL_SETTINGS": None,
    }
    assert any("Choose No to configure a separate Reranking model" in prompt for prompt in ui.prompts)


def test_existing_separate_reranking_model_is_preserved_by_default() -> None:
    values = {
        PREFIX + "GENERATION_MODEL": "openai-chat:generation-model",
        PREFIX + "RERANK_MODEL": "bedrock:rerank-model",
        PREFIX + "RERANK_MODEL_SETTINGS": '{"temperature":0.5}',
    }

    assert collect_models(Answers(True, True), values, generation=True, embedding=False, rerank=True) == {}


def test_anthropic_generation_does_not_offer_unsupported_embedding_connection_sharing() -> None:
    ui = Answers(
        "anthropic",
        "",
        "claude-sonnet-4-5",
        "generation-key",
        "bailian",
        "",
        "text-embedding-v4",
        "embedding-key",
    )

    updates = collect_models(ui, {}, generation=True, embedding=True)

    assert updates[PREFIX + "GENERATION_MODEL"] == "anthropic:claude-sonnet-4-5"
    assert updates[PREFIX + "EMBEDDING_MODEL"] == "openai:text-embedding-v4"
    assert updates[PREFIX + "EMBEDDING_PROFILE_ID"] == "openai-text-embedding-v4-1024-unit-v1"
    assert not any("same API" in prompt for prompt in ui.prompts)


def test_openrouter_uses_a_runtime_supported_adapter_for_workload_overrides() -> None:
    ui = Answers("openrouter", "", "deepseek/deepseek-v4-pro", "openrouter-key")

    updates = collect_models(ui, {}, generation=True, embedding=False)

    assert updates[PREFIX + "GENERATION_MODEL"] == "openai-chat:deepseek/deepseek-v4-pro"
    assert updates[PREFIX + "GENERATION_BASE_URL"] == "https://openrouter.ai/api/v1"


def test_custom_service_collects_protocol_in_addition_to_endpoint_model_and_key() -> None:
    ui = Answers("custom", "openai-responses", "https://custom.example/v1", "custom-model", "custom-key")

    updates = collect_models(ui, {}, generation=True, embedding=False)

    assert updates[PREFIX + "GENERATION_MODEL"] == "openai:custom-model"
    assert updates[PREFIX + "GENERATION_BASE_URL"] == "https://custom.example/v1"


def test_shared_existing_openai_credentials_are_copied_only_to_embedding_role() -> None:
    values = {
        PREFIX + "GENERATION_MODEL": "openai-chat:existing-model",
        "OPENAI_API_KEY": "legacy-key",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
    }
    ui = Answers(True, True, "text-embedding-3-small", 1536, "")

    updates = collect_models(ui, values, generation=True, embedding=True)

    assert json.loads(updates[PREFIX + "EMBEDDING_HEADERS"] or "") == {"Authorization": "Bearer legacy-key"}
    assert all(key.startswith(PREFIX + "EMBEDDING_") for key in updates)


def test_a_control_character_in_a_key_is_reprompted_without_echo() -> None:
    invalid_key = "key\r\nx-injected-header:value"
    ui = Answers("bailian", "", "qwen-plus", invalid_key, "valid-key")

    updates = collect_models(ui, {}, generation=True, embedding=False)

    assert json.loads(updates[PREFIX + "GENERATION_HEADERS"] or "") == {"Authorization": "Bearer valid-key"}
    assert all(invalid_key not in prompt for prompt in ui.prompts)


def test_basic_capabilities_do_not_collect_or_modify_any_models() -> None:
    values = {PREFIX + "GENERATION_MODEL": "openai-chat:existing-model"}

    assert collect_models(Answers(), values, generation=False, embedding=False) == {}
    assert values == {PREFIX + "GENERATION_MODEL": "openai-chat:existing-model"}
