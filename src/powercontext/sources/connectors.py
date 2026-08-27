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

"""Provider-neutral Connector run and durable checkpoint contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.errors import InvalidConnectorError, InvalidConnectorRunError
from powercontext.limits import MAX_SCOPE_ID_LENGTH, MAX_SOURCE_ID_LENGTH, MAX_SOURCE_TYPE_LENGTH
from powercontext.sources.catalog import SourceCatalog
from powercontext.sources.models import Source, SourceRef
from powercontext.sources.protocols import SourceStore


class ConnectorCapability(StrEnum):
    """Acquisition guarantees a Connector can actually enforce."""

    COMPLETE_SNAPSHOT = "complete_snapshot"
    CHANGE_FEED = "change_feed"
    CHECKPOINT_RESUME = "checkpoint_resume"
    AUTHORITATIVE_DELETION = "authoritative_deletion"


class ConnectorRunStatus(StrEnum):
    """Whether the Connector completed the provider work represented by a run."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class ConnectorSubmissionStatus(StrEnum):
    """Durable outcome for one definition-native item submission."""

    ACCEPTED = "accepted"
    REPLAYED = "replayed"
    REJECTED = "rejected"
    FAILED = "failed"


class ConnectorBinding(BaseModel):
    """Activate one Connector identity for exactly one Scope."""

    model_config = ConfigDict(frozen=True)

    scope_id: str = Field(max_length=MAX_SCOPE_ID_LENGTH)
    binding_id: str = Field(max_length=MAX_SOURCE_ID_LENGTH)
    connector_name: str = Field(max_length=MAX_SOURCE_TYPE_LENGTH)
    connector_version: str = Field(max_length=MAX_SOURCE_TYPE_LENGTH)

    @field_validator("scope_id", "binding_id", "connector_name", "connector_version")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Connector identity must be non-empty")  # noqa: TRY003
        if value != value.strip():
            raise ValueError("Connector identity must be trimmed")  # noqa: TRY003
        return value


class ConnectorSubmissionResult(BaseModel):
    """Sink result after one item has reached a durable acceptance boundary."""

    model_config = ConfigDict(frozen=True)

    status: ConnectorSubmissionStatus
    source_ref: SourceRef | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_source_ref(self) -> ConnectorSubmissionResult:
        accepted = self.status in {ConnectorSubmissionStatus.ACCEPTED, ConnectorSubmissionStatus.REPLAYED}
        if accepted != (self.source_ref is not None):
            raise ValueError("accepted and replayed submissions require exactly one SourceRef")  # noqa: TRY003
        return self


class ConnectorItemOutcome(BaseModel):
    """Visible result for every item a Connector submitted during one run."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    definition_name: str
    status: ConnectorSubmissionStatus
    source_ref: SourceRef | None = None
    detail: str | None = None


class ConnectorRunCompletion(BaseModel):
    """Connector-owned completion signal and next opaque checkpoint."""

    model_config = ConfigDict(frozen=True)

    status: ConnectorRunStatus
    checkpoint: JsonValue | None = None


class ConnectorRunResult(BaseModel):
    """Observable lifecycle result after any safe checkpoint commit."""

    model_config = ConfigDict(frozen=True)

    binding: ConnectorBinding
    status: ConnectorRunStatus
    previous_checkpoint: JsonValue | None
    proposed_checkpoint: JsonValue | None
    committed_checkpoint: JsonValue | None
    items: tuple[ConnectorItemOutcome, ...]


class ConnectorSourceSink(Protocol):
    """Accept definition-native input and return its durable local SourceRef."""

    async def submit(
        self,
        binding: ConnectorBinding,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> ConnectorSubmissionResult: ...


class ConnectorCheckpointStore(Protocol):
    """Persist opaque binding checkpoints using optimistic comparison."""

    async def load(self, binding: ConnectorBinding, /) -> JsonValue | None: ...

    async def save(
        self,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        /,
        *,
        expected: JsonValue | None,
    ) -> None: ...


class Connector(Protocol):
    """Acquire provider items through one lifecycle session."""

    name: str
    version: str
    source_definitions: frozenset[str]
    capabilities: frozenset[ConnectorCapability]

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion: ...


class ConnectorRunSession:
    """Constrain one Connector run to declared Definitions and visible outcomes."""

    def __init__(
        self,
        *,
        binding: ConnectorBinding,
        checkpoint: JsonValue | None,
        source_definitions: frozenset[str],
        sink: ConnectorSourceSink,
    ) -> None:
        self.binding = binding
        self.checkpoint = checkpoint
        self._source_definitions = source_definitions
        self._sink = sink
        self._outcomes: list[ConnectorItemOutcome] = []
        self._item_ids: set[str] = set()

    @property
    def outcomes(self) -> tuple[ConnectorItemOutcome, ...]:
        return tuple(self._outcomes)

    async def submit(
        self,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> ConnectorSubmissionResult:
        """Submit one item and record success, rejection, or sink failure exactly once."""

        self._claim_item(item_id, definition_name)
        try:
            result = await self._sink.submit(self.binding, item_id, definition_name, value)
        except Exception as error:
            result = ConnectorSubmissionResult(
                status=ConnectorSubmissionStatus.FAILED,
                detail=type(error).__name__,
            )
        if result.source_ref is not None and result.source_ref.source_type != definition_name:
            raise InvalidConnectorRunError(
                "definition-mismatch",
                f"sink returned {result.source_ref.source_type!r} for {definition_name!r}",
            )
        self._outcomes.append(
            ConnectorItemOutcome(
                item_id=item_id,
                definition_name=definition_name,
                status=result.status,
                source_ref=result.source_ref,
                detail=result.detail,
            )
        )
        return result

    def reject(self, item_id: str, definition_name: str, detail: str, /) -> ConnectorSubmissionResult:
        """Record one provider item that cannot satisfy its Source Definition."""

        return self._record_provider_outcome(
            item_id,
            definition_name,
            ConnectorSubmissionStatus.REJECTED,
            detail,
        )

    def fail(self, item_id: str, definition_name: str, detail: str, /) -> ConnectorSubmissionResult:
        """Record one provider item that could not be acquired safely."""

        return self._record_provider_outcome(
            item_id,
            definition_name,
            ConnectorSubmissionStatus.FAILED,
            detail,
        )

    def _record_provider_outcome(
        self,
        item_id: str,
        definition_name: str,
        status: ConnectorSubmissionStatus,
        detail: str,
    ) -> ConnectorSubmissionResult:
        _require_trimmed("detail", detail)
        if status not in {ConnectorSubmissionStatus.REJECTED, ConnectorSubmissionStatus.FAILED}:
            raise InvalidConnectorRunError("provider-outcome", "must be rejected or failed")
        self._claim_item(item_id, definition_name)
        result = ConnectorSubmissionResult(status=status, detail=detail)
        self._outcomes.append(
            ConnectorItemOutcome(
                item_id=item_id,
                definition_name=definition_name,
                status=status,
                detail=detail,
            )
        )
        return result

    def _claim_item(self, item_id: str, definition_name: str) -> None:
        _require_trimmed("item_id", item_id)
        if item_id in self._item_ids:
            raise InvalidConnectorRunError("duplicate-item", f"item {item_id!r} was submitted more than once")
        if definition_name not in self._source_definitions:
            raise InvalidConnectorRunError(
                "undeclared-definition",
                f"Connector did not declare Source Definition {definition_name!r}",
            )
        self._item_ids.add(item_id)


class ConnectorLifecycle:
    """Run Connectors while enforcing durable checkpoint ordering."""

    def __init__(self, *, sink: ConnectorSourceSink, checkpoints: ConnectorCheckpointStore) -> None:
        self._sink = sink
        self._checkpoints = checkpoints

    async def run(self, connector: Connector, binding: ConnectorBinding, /) -> ConnectorRunResult:
        source_definitions, capabilities = validate_connector(connector, binding)
        previous = await self._checkpoints.load(binding)
        if previous is not None and ConnectorCapability.CHECKPOINT_RESUME not in capabilities:
            raise InvalidConnectorRunError(
                "unsupported-resume",
                "binding has a checkpoint but Connector does not advertise checkpoint resume",
            )
        session = ConnectorRunSession(
            binding=binding,
            checkpoint=previous,
            source_definitions=source_definitions,
            sink=self._sink,
        )
        completion = await connector.run(session)
        if not isinstance(completion, ConnectorRunCompletion):
            raise InvalidConnectorRunError("completion", "Connector must return ConnectorRunCompletion")

        unsafe = tuple(
            outcome
            for outcome in session.outcomes
            if outcome.status in {ConnectorSubmissionStatus.REJECTED, ConnectorSubmissionStatus.FAILED}
        )
        committed = previous
        if completion.status is ConnectorRunStatus.COMPLETE and completion.checkpoint != previous and not unsafe:
            await self._checkpoints.save(binding, completion.checkpoint, expected=previous)
            committed = completion.checkpoint
        return ConnectorRunResult(
            binding=binding,
            status=ConnectorRunStatus.INCOMPLETE if unsafe else completion.status,
            previous_checkpoint=previous,
            proposed_checkpoint=completion.checkpoint,
            committed_checkpoint=committed,
            items=session.outcomes,
        )


class CatalogConnectorSourceSink:
    """Bridge lifecycle submissions to one scope-bound catalog and Source store."""

    def __init__(self, *, scope_id: str, catalog: SourceCatalog, store: SourceStore[Source]) -> None:
        _require_trimmed("scope_id", scope_id)
        self._scope_id = scope_id
        self._catalog = catalog
        self._store = store

    async def submit(
        self,
        binding: ConnectorBinding,
        item_id: str,
        definition_name: str,
        value: object,
        /,
    ) -> ConnectorSubmissionResult:
        del item_id
        if binding.scope_id != self._scope_id:
            raise InvalidConnectorRunError(
                "scope-mismatch",
                f"sink is bound to {self._scope_id!r}, got {binding.scope_id!r}",
            )
        source = await self._catalog.resolve(value)
        source_ref = self._catalog.as_ref(source)
        if source_ref.source_type != definition_name:
            raise InvalidConnectorRunError(
                "definition-mismatch",
                f"input resolved as {source_ref.source_type!r}, expected {definition_name!r}",
            )
        stored = await self._store.add(source)
        stored_ref = self._catalog.as_ref(stored)
        if stored_ref != source_ref:
            raise InvalidConnectorRunError("identity-mismatch", "Source store changed the accepted identity")
        return ConnectorSubmissionResult(status=ConnectorSubmissionStatus.ACCEPTED, source_ref=stored_ref)


def validate_connector(
    connector: Connector,
    binding: ConnectorBinding,
) -> tuple[frozenset[str], frozenset[ConnectorCapability]]:
    """Validate one Connector declaration against the binding it will execute."""

    name = getattr(connector, "name", None)
    version = getattr(connector, "version", None)
    if name != binding.connector_name:
        raise InvalidConnectorError("name", f"binding expects {binding.connector_name!r}, got {name!r}")
    if version != binding.connector_version:
        raise InvalidConnectorError("version", f"binding expects {binding.connector_version!r}, got {version!r}")
    source_definitions = getattr(connector, "source_definitions", None)
    if not isinstance(source_definitions, frozenset) or not source_definitions:
        raise InvalidConnectorError("source_definitions", "must be a non-empty frozenset")
    if not all(isinstance(value, str) and value.strip() == value and value for value in source_definitions):
        raise InvalidConnectorError("source_definitions", "must contain non-empty trimmed names")
    capabilities = getattr(connector, "capabilities", None)
    if not isinstance(capabilities, frozenset) or not all(
        isinstance(value, ConnectorCapability) for value in capabilities
    ):
        raise InvalidConnectorError("capabilities", "must be a frozenset of ConnectorCapability values")
    if not callable(getattr(connector, "run", None)):
        raise InvalidConnectorError("run", "must be callable")
    return source_definitions, capabilities


def _require_trimmed(field: str, value: object) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidConnectorRunError(field, "must be a non-empty trimmed string")
