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

"""The recall embedding budget is a bounded runtime setting with an environment mapping."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server.settings import ServerSettings


def test_recall_embedding_timeout_defaults_to_the_hook_sized_budget() -> None:
    assert RuntimeConfig().recall_embedding_timeout_seconds == 0.5


@pytest.mark.parametrize("value", [0, -1, 61, float("nan"), True])
def test_recall_embedding_timeout_rejects_values_outside_the_budget_range(value: Any) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(recall_embedding_timeout_seconds=value)


def test_recall_embedding_timeout_is_read_from_the_server_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_RECALL_EMBEDDING_TIMEOUT_SECONDS", "1.5")

    assert ServerSettings().runtime.recall_embedding_timeout_seconds == 1.5
