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

"""Configuration-boundary tests for the standalone LangChain package."""

from powercontext_langchain import PowerContextLangChainSettings, PowerContextScope
from powercontext_langchain.client import resolve_config
from pydantic import SecretStr


def test_langchain_settings_use_their_own_environment_prefix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    credential = "test-credential"
    monkeypatch.setenv("POWERCONTEXT_LANGCHAIN_BASE_URL", "https://langchain.example")
    monkeypatch.setenv("POWERCONTEXT_LANGCHAIN_SCOPE_ID", "project:langchain")
    monkeypatch.setenv("POWERCONTEXT_LANGCHAIN_TOKEN", credential)
    monkeypatch.setenv("POWERCONTEXT_LANGGRAPH_SCOPE_ID", "project:langgraph")

    config = resolve_config()

    assert config.base_url == "https://langchain.example"
    assert config.scope_id == "project:langchain"
    assert config.token == credential


def test_explicit_scope_overrides_langchain_settings() -> None:
    scope_credential = "scope-credential"
    settings = PowerContextLangChainSettings(
        base_url="https://settings.example",
        scope_id="project:settings",
        token=SecretStr("settings-token"),
        timeout=10,
    )

    config = resolve_config(
        PowerContextScope(
            base_url="https://scope.example",
            scope_id="project:scope",
            token=scope_credential,
            timeout=2,
        ),
        settings=settings,
    )

    assert config.base_url == "https://scope.example"
    assert config.scope_id == "project:scope"
    assert config.token == scope_credential
    assert config.timeout == 2
