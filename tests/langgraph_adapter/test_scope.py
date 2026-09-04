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

"""Scope configuration tests for the PowerContext LangGraph adapter."""

from __future__ import annotations

import pytest

pytest.importorskip("powercontext_langgraph")

from powercontext_langgraph import PowerContextScope
from powercontext_langgraph.client import resolve_config
from powercontext_langgraph.settings import PowerContextLangGraphSettings

TEST_TOKEN = "langgraph-scope-test-token"  # noqa: S105 - non-secret test credential.


def test_run_scope_overrides_environment_settings() -> None:
    settings = PowerContextLangGraphSettings(
        base_url="https://server.example",
        scope_id="scp_environment",
        timeout=20,
    )

    config = resolve_config(
        PowerContextScope(
            scope_id="  scp_run  ",
            base_url="https://run.example",
            token=TEST_TOKEN,
            timeout=3,
        ),
        settings=settings,
    )

    assert config.base_url == "https://run.example"
    assert config.scope_id == "scp_run"
    assert config.token == TEST_TOKEN
    assert config.timeout == 3


def test_blank_scope_uses_server_default() -> None:
    config = resolve_config(
        PowerContextScope(scope_id="   "),
        settings=PowerContextLangGraphSettings(scope_id=None),
    )

    assert config.scope_id is None


def test_scope_token_is_hidden_from_repr() -> None:
    scope = PowerContextScope(token=TEST_TOKEN)

    assert TEST_TOKEN not in repr(scope)
