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

from pydantic import JsonValue
from typing_extensions import override

from powercontext.client.client import PowerContextClient
from powercontext.errors import InvalidConnectorRunError
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
    ProjectedSource as HttpProjectedSource,
)
from powercontext.http import (
    SourceDefinitionManifest as HttpSourceDefinitionManifest,
)
from powercontext.sources import (
    Connector,
    ConnectorBinding,
    ConnectorCheckpointStore,
    ConnectorLifecycle,
    ConnectorRunResult,
    ConnectorSourceSink,
    ConnectorSubmissionResult,
    ConnectorSubmissionStatus,
    SourceDefinitionRegistry,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
    validate_connector,
)


class RemoteConnectorSourceSink(ConnectorSourceSink):
    """Resolve and project Definition-native values inside the worker."""

    def __init__(self, *, client: PowerContextClient, registry: SourceDefinitionRegistry) -> None:
        self._client = client
        self._registry = registry

    @override
    async def submit(
        self,
        binding: ConnectorBinding,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> ConnectorSubmissionResult:
        del item_id
        self._registry.definition_for_name(definition_name)
        source = await self._registry.resolve(value)
        projected = project_source_for_transport(self._registry, source)
        if projected.source_type != definition_name:
            raise InvalidConnectorRunError(
                "definition-mismatch",
                f"input resolved as {projected.source_type!r}, expected {definition_name!r}",
            )
        receipt = await self._client.submit_source_observation(
            SubmitSourceObservationRequest(
                binding=_http_binding(binding),
                source=HttpProjectedSource.model_validate(projected.model_dump(mode="json")),
            )
        )
        source_ref = SourceRef(source_type=receipt.source.name, source_id=receipt.source.source_id)
        expected_ref = SourceRef(source_type=projected.source_type, source_id=projected.name)
        if source_ref != expected_ref:
            raise InvalidConnectorRunError("identity-mismatch", "Server receipt changed the accepted Source identity")
        return ConnectorSubmissionResult(status=ConnectorSubmissionStatus.ACCEPTED, source_ref=source_ref)


class RemoteConnectorCheckpointStore(ConnectorCheckpointStore):
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
    """Register worker-owned Definitions and execute one Connector binding."""

    def __init__(self, *, client: PowerContextClient, registry: SourceDefinitionRegistry) -> None:
        self._client = client
        self._registry = registry
        self._lifecycle = ConnectorLifecycle(
            sink=RemoteConnectorSourceSink(client=client, registry=registry),
            checkpoints=RemoteConnectorCheckpointStore(client),
        )

    async def run(self, connector: Connector, binding: ConnectorBinding, /) -> ConnectorRunResult:
        source_definitions, _ = validate_connector(connector, binding)
        for definition_name in sorted(source_definitions):
            definition = self._registry.definition_for_name(definition_name)
            manifest = manifest_for_definition(definition)
            registered = await self._client.register_source_definition(
                RegisterSourceDefinitionRequest(
                    manifest=HttpSourceDefinitionManifest.model_validate(
                        manifest.model_dump(mode="json", by_alias=True)
                    )
                )
            )
            if registered.model_dump(mode="json", by_alias=True) != manifest.model_dump(mode="json", by_alias=True):
                raise InvalidConnectorRunError(
                    "manifest-mismatch",
                    f"Server returned a different manifest for {definition.name!r}",
                )
        return await self._lifecycle.run(connector, binding)


def _http_binding(binding: ConnectorBinding) -> HttpConnectorBinding:
    return HttpConnectorBinding.model_validate(binding.model_dump(mode="json"))


def _validate_checkpoint_binding(
    expected_binding: ConnectorBinding,
    actual_binding: HttpConnectorBinding,
) -> None:
    if actual_binding.model_dump(mode="json") != expected_binding.model_dump(mode="json"):
        raise InvalidConnectorRunError("binding-mismatch", "Server returned a different Connector binding")


__all__ = [
    "RemoteConnectorCheckpointStore",
    "RemoteConnectorSourceSink",
    "RemoteConnectorWorker",
]
