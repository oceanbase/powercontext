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

"""Approved operation configuration survives serialization and respects explicit overrides."""

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime.config import InferenceConfig


def test_operation_modes_override_only_the_named_atomic_stage_and_survive_roundtrip():
    config = InferenceConfig(
        atomic_memory_input_encoding="active",
        input_encoding={"atomic_memory.extract": "off", "experience.generate": "shadow"},
    )
    restored = InferenceConfig.model_validate_json(config.model_dump_json())
    assert restored.input_encoding_mode("atomic_memory.extract") == "off"
    assert restored.input_encoding_mode("atomic_memory.reconcile") == "active"
    assert restored.input_encoding_mode("experience.generate") == "shadow"
    assert restored.input_encoding_mode("experience.incubate") == "off"


@pytest.mark.parametrize("operation", ["skill.generate", "experience.generat", "unregistered"])
def test_unintegrated_or_misspelled_operations_are_rejected(operation):
    with pytest.raises(ValidationError):
        InferenceConfig.model_validate({"input_encoding": {operation: "active"}})


def test_all_operations_are_disabled_by_default():
    config = InferenceConfig()
    for operation in ("atomic_memory.extract", "atomic_memory.reconcile", "experience.generate", "experience.incubate"):
        assert config.input_encoding_mode(operation) == "off"
