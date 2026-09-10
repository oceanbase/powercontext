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
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


def processing_capabilities(config: BuiltinConfig) -> tuple[str, ...]:
    """Use explicit cross-role declarations or infer executable built-ins."""

    # Distributed v1 executes Memory, Experience, and Profile work through the
    # database-backed Work Ledger. The process-local Artifact Processing
    # Supervisor remains the single-node execution engine.
    if config.deployment.mode == "distributed":
        return ()
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
    return tuple(sorted(families))


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
        },
        "legacy_automatic_bindings": sorted(automatic),
    }
