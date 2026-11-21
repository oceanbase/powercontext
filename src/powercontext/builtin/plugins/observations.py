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

"""Turn a plugin's Source values into stored observations with the Server's own plugin version.

Projections are computed when a Source is written, by the plugin the Server loaded, so
delivery only reads saved results. Host processes use this for Source writes and runner
observations; collection Workers use it for what the Server's own runner collects.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, JsonValue

from powercontext.sources.observations import SourceObservation


class ObservationRejected(ValueError):
    """The observation does not match a Source Definition of the loaded plugin version."""


def _manifest(manifests: Sequence[dict[str, JsonValue]], name: str) -> Any:
    from powercontext.sources.observations import SourceDefinitionManifest

    for item in manifests:
        if item["name"] == name:
            return SourceDefinitionManifest.model_validate(item)
    raise ObservationRejected(f"{name} is not a Source type of this plugin")  # noqa: TRY003


async def observe(
    registry: Any, manifests: Sequence[dict[str, JsonValue]], definition: str, value: BaseModel
) -> SourceObservation:
    """Resolve an input of one of the plugin's Source types and project it."""

    from powercontext.builtin.sources.observation_validation import validate_source_observation
    from powercontext.sources.observations import project_source_for_transport

    selected = registry.definition_for_name(definition)
    if not isinstance(value, selected.input_class):
        raise ObservationRejected(f"{definition} expects {selected.input_class.__name__}")  # noqa: TRY003
    observation = project_source_for_transport(registry, await registry.resolve(value))
    validate_source_observation(observation, _manifest(manifests, definition))
    return observation


def reproject(
    registry: Any, manifests: Sequence[dict[str, JsonValue]], observation: SourceObservation
) -> SourceObservation:
    """Rebuild a runner's observation from its canonical value and project it with this plugin version."""

    from powercontext.builtin.sources.observation_validation import validate_source_observation
    from powercontext.sources.observations import project_source_for_transport

    try:
        definition = registry.definition_for_name(observation.source_type)
    except Exception:
        raise ObservationRejected(f"{observation.source_type} is not a Source type of this plugin") from None  # noqa: TRY003
    if definition.version != observation.definition_version:
        raise ObservationRejected("the runner's Source Definition version is not the one this Server loaded")  # noqa: TRY003
    source = definition.source_class.model_validate(observation.payload)
    projected = project_source_for_transport(registry, source)
    validate_source_observation(projected, _manifest(manifests, observation.source_type))
    return projected
