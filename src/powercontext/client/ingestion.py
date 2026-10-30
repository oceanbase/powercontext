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

"""Run worker-owned Connectors against the remote ingestion contract."""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue
from typing_extensions import override

from powercontext.client.client import PowerContextClient
from powercontext.errors import ConnectorSubmissionRejectedError, InvalidConnectorRunError
from powercontext.http import (
    CommitConnectorCheckpointRequest,
    GetConnectorCheckpointRequest,
    RegisterSourceDefinitionRequest,
    SubmitSourceObservationRequest,
)
from powercontext.http import (
    ConnectorBinding as HttpConnectorBinding,
)
from powercontext.http import (
    SourceDefinitionManifest as HttpSourceDefinitionManifest,
)
from powercontext.http import (
    SourceObservation as HttpSourceObservation,
)
from powercontext.limits import MAX_SOURCE_OBSERVATION_BYTES
from powercontext.sources import (
    Connector,
    ConnectorBinding,
    ConnectorRunResult,
    SourceDefinitionRegistry,
    SourceObservation,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.connectors import (
    ConnectorCheckpointStore,
    ConnectorLifecycle,
    ConnectorRunObserver,
    ConnectorSourceSink,
    validate_connector,
)


class RemoteConnectorObserver(ConnectorRunObserver, Protocol):
    """Observe materialization and confirmed submission without changing Source semantics."""

    async def materialized(
        self, binding: ConnectorBinding, item_id: str, observation: SourceObservation, /
    ) -> None: ...

    async def accepted(self, item_id: str, source_ref: SourceRef, /) -> None: ...


class _RemoteConnectorSourceSink(ConnectorSourceSink):
    """Resolve and project Definition-native values inside the worker."""

    def __init__(
        self,
        *,
        client: PowerContextClient,
        registry: SourceDefinitionRegistry,
        observer: RemoteConnectorObserver | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._observer = observer

    @override
    async def submit(
        self,
        binding: ConnectorBinding,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> SourceRef:
        self._registry.definition_for_name(definition_name)
        source = await self._registry.resolve(value)
        observation: SourceObservation = project_source_for_transport(self._registry, source)
        if observation.source_type != definition_name:
            raise InvalidConnectorRunError(
                "definition-mismatch",
                f"input resolved as {observation.source_type!r}, expected {definition_name!r}",
            )
        if len(observation.model_dump_json().encode()) > MAX_SOURCE_OBSERVATION_BYTES:
            raise ConnectorSubmissionRejectedError(  # noqa: TRY003
                f"observation exceeds {MAX_SOURCE_OBSERVATION_BYTES} bytes"
            )
        if self._observer is not None:
            await self._observer.materialized(binding, item_id, observation)
        source_ref = await submit_materialized_observation(self._client, binding, observation)
        if self._observer is not None:
            await self._observer.accepted(item_id, source_ref)
        return source_ref


async def submit_materialized_observation(
    client: PowerContextClient, binding: ConnectorBinding, observation: SourceObservation
) -> SourceRef:
    """Submit exactly the captured value, including its stable Source identity."""
    receipt = await client.submit_source_observation(
        SubmitSourceObservationRequest(
            scope_id=binding.scope_id,
            observation=HttpSourceObservation.model_validate(observation.model_dump(mode="json")),
        )
    )
    source_ref = SourceRef(source_type=receipt.source.name, source_id=receipt.source.source_id)
    expected_ref = SourceRef(source_type=observation.source_type, source_id=observation.name)
    if source_ref != expected_ref:
        raise InvalidConnectorRunError("identity-mismatch", "Server receipt changed the accepted Source identity")
    return source_ref


class _RemoteConnectorCheckpointStore(ConnectorCheckpointStore):
    """Load and compare-and-swap opaque checkpoints through the Server API."""

    def __init__(self, client: PowerContextClient) -> None:
        self._client = client

    @override
    async def load(self, binding: ConnectorBinding, /) -> JsonValue | None:
        state = await self._client.get_connector_checkpoint(
            GetConnectorCheckpointRequest(binding=_http_binding(binding))
        )
        _validate_checkpoint_binding(binding, state.binding)
        return state.checkpoint

    @override
    async def save(
        self,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> None:
        state = await self._client.commit_connector_checkpoint(
            CommitConnectorCheckpointRequest(
                binding=_http_binding(binding),
                expected=expected,
                checkpoint=checkpoint,
            )
        )
        _validate_checkpoint_binding(binding, state.binding)
        if state.checkpoint != checkpoint:
            raise InvalidConnectorRunError("checkpoint-mismatch", "Server returned a different Connector checkpoint")


class RemoteConnectorWorker:
    """Register connector Definitions and execute one binding.

    Managed runtimes disable registration after deployment registers their Definitions,
    allowing collection with Scope credentials alone.
    """

    def __init__(
        self,
        *,
        client: PowerContextClient,
        registry: SourceDefinitionRegistry,
        observer: RemoteConnectorObserver | None = None,
        register_definitions: bool = True,
    ) -> None:
        self._client = client
        self._registry = registry
        self._observer = observer
        self._register_definitions = register_definitions
        self._lifecycle = ConnectorLifecycle(
            sink=_RemoteConnectorSourceSink(client=client, registry=registry, observer=observer),
            checkpoints=_RemoteConnectorCheckpointStore(client),
        )

    async def run(self, connector: Connector, binding: ConnectorBinding, /) -> ConnectorRunResult:
        definitions = tuple(
            self._registry.definition_for_name(name) for name in sorted(validate_connector(connector, binding))
        )
        if self._register_definitions:
            await register_source_definitions(client=self._client, registry=SourceDefinitionRegistry(definitions))
        return await self._lifecycle.run(connector, binding, observer=self._observer)


async def register_source_definitions(*, client: PowerContextClient, registry: SourceDefinitionRegistry) -> None:
    """Register trusted output contracts using a deployment credential, before collection."""
    for definition in registry.definitions:
        manifest = manifest_for_definition(definition)
        registered = await client.register_source_definition(
            RegisterSourceDefinitionRequest(
                manifest=HttpSourceDefinitionManifest.model_validate(manifest.model_dump(mode="json", by_alias=True))
            )
        )
        if registered.model_dump(mode="json", by_alias=True) != manifest.model_dump(mode="json", by_alias=True):
            raise InvalidConnectorRunError("manifest-mismatch", "Server returned a different Definition manifest")


def _http_binding(binding: ConnectorBinding) -> HttpConnectorBinding:
    return HttpConnectorBinding.model_validate(binding.model_dump(mode="json"))


def _validate_checkpoint_binding(
    expected_binding: ConnectorBinding,
    actual_binding: HttpConnectorBinding,
) -> None:
    if actual_binding.model_dump(mode="json") != expected_binding.model_dump(mode="json"):
        raise InvalidConnectorRunError("binding-mismatch", "Server returned a different Connector binding")


__all__ = ["RemoteConnectorWorker", "register_source_definitions"]
