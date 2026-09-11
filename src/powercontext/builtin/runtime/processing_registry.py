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

"""Stable deployment identity shared by startup and maintenance tooling."""

from __future__ import annotations

from typing import Any

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.dream.bindings import DREAM_PROVIDERS, SKILL_DREAM_BINDING
from powercontext.builtin.dream.models import DreamOperation
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

# Provisional per-Family recommendations for operator-facing setup tools. Runtime
# defaults remain disabled; Artifact owners can revise these independently after
# production workload review without changing the Supervisor contract.
RECOMMENDED_PROCESSING_SCHEDULE_SECONDS = {
    "memory": 60,
    "topic-memory": 300,
    "experience": 900,
}
RECOMMENDED_PROFILE_CRON = "0 2 * * *"
RECOMMENDED_PROFILE_TIMEZONE = "Asia/Shanghai"


def processing_capabilities(config: BuiltinConfig) -> tuple[str, ...]:
    """Use explicit cross-role declarations or infer executable built-ins."""

    declared = config.runtime.artifact_processing_families
    if declared is not None:
        return tuple(sorted(declared))
    if config.inference.generation_model is None:
        return ()
    from powercontext.builtin.runtime.composition import BuiltinConfigurationError
    from powercontext.builtin.runtime.topic_memory_processing import validate_topic_memory_provider_settings

    families = ["memory", "experience", "profile"]
    try:
        validate_topic_memory_provider_settings(config.inference)
    except BuiltinConfigurationError:
        pass
    else:
        families.append("topic-memory")
    if config.runtime.dream_enabled and config.inference.generation_model.split(":", 1)[0] in DREAM_PROVIDERS:
        families.append("skill")
    return tuple(sorted(families))


def dream_operations(config: BuiltinConfig) -> tuple[DreamOperation, ...]:
    """Declare admission independently of API-side model construction."""

    if not config.runtime.dream_enabled:
        return ()
    if config.runtime.artifact_processing_role != "api" and (
        config.inference.generation_model is None
        or config.inference.generation_model.split(":", 1)[0] not in DREAM_PROVIDERS
    ):
        return ()
    families = processing_capabilities(config)
    return tuple(
        operation
        for operation, family in (("refine_experience", "experience"), ("derive_skill", "skill"))
        if family in families
    )


def canonical_processing_manifest(config: BuiltinConfig) -> dict[str, Any]:
    """Identify ownership configuration without freezing execution-only budgets."""

    runtime = config.runtime
    # Target schedules do not prove that the stopped deployment had no active
    # automatic Topic work. Preserve recovery responsibility conservatively.
    automatic = [TOPIC_MEMORY_SOURCE_WINDOW_BINDING]
    if runtime.schedule_seconds is not None:
        automatic.append(SOURCE_WINDOW_TRIGGER_NAME)
    if runtime.experience_schedule_seconds is not None:
        automatic.append(EXPERIENCE_INCUBATION_CURSOR_NAME)
    if runtime.profile_schedule_enabled:
        automatic.append(PROFILE_SOURCE_WINDOW_BINDING)
    return {
        "mode": runtime.artifact_processing_supervisor_mode,
        "capabilities": list(processing_capabilities(config)),
        "bindings": {
            SOURCE_WINDOW_TRIGGER_NAME: "memory",
            TOPIC_MEMORY_SOURCE_WINDOW_BINDING: "topic-memory",
            EXPERIENCE_INCUBATION_CURSOR_NAME: "experience",
            PROFILE_SOURCE_WINDOW_BINDING: "profile",
            SKILL_DREAM_BINDING: "skill",
        },
        "legacy_automatic_bindings": sorted(automatic),
    }
