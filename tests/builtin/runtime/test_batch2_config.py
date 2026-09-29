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

from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.settings import ServerSettings


def test_memory_write_gate_is_disabled_by_default() -> None:
    config = RuntimeConfig()

    assert config.memory_write_gate_enabled is False
    assert config.memory_write_gate_hold_on == "yes"
    assert config.memory_write_gate_threshold is None


def test_the_hold_direction_is_configurable() -> None:
    assert RuntimeConfig(memory_write_gate_hold_on="no").memory_write_gate_hold_on == "no"


def test_an_unknown_hold_direction_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"memory_write_gate_hold_on": "maybe"})


@pytest.mark.parametrize("threshold", [-0.1, 1.1])
def test_the_strength_threshold_is_bounded(threshold: float) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"memory_write_gate_threshold": threshold})


def test_the_gate_settings_load_from_the_server_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_ENABLED", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_HOLD_ON", "no")

    runtime = ServerSettings().runtime

    assert runtime.memory_write_gate_enabled is True
    assert runtime.memory_write_gate_hold_on == "no"
