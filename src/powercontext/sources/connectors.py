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

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from powercontext.errors import ConnectorSubmissionRejectedError, InvalidConnectorError, InvalidConnectorRunError
from powercontext.limits import MAX_SCOPE_ID_LENGTH, MAX_SOURCE_ID_LENGTH, MAX_SOURCE_TYPE_LENGTH
from powercontext.sources.models import SourceRef


class ConnectorRunStatus(StrEnum):
    """Whether the Connector completed the provider work represented by a run."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class ConnectorSubmissionStatus(StrEnum):
    """Durable outcome for one definition-native item submission."""

    ACCEPTED = "accepted"
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
    ) -> SourceRef: ...


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
    ) -> ConnectorItemOutcome:
        """Submit one item and record its durable Source reference exactly once."""

        self._claim_item(item_id, definition_name)
        try:
            source_ref = await self._sink.submit(self.binding, item_id, definition_name, value)
        except ConnectorSubmissionRejectedError as error:
            outcome = ConnectorItemOutcome(
                item_id=item_id,
                definition_name=definition_name,
                status=ConnectorSubmissionStatus.REJECTED,
                detail=error.detail,
            )
            self._outcomes.append(outcome)
            return outcome
        if source_ref.source_type != definition_name:
            raise InvalidConnectorRunError(
                "definition-mismatch",
                f"sink returned {source_ref.source_type!r} for {definition_name!r}",
            )
        outcome = ConnectorItemOutcome(
            item_id=item_id,
            definition_name=definition_name,
            status=ConnectorSubmissionStatus.ACCEPTED,
            source_ref=source_ref,
        )
        self._outcomes.append(outcome)
        return outcome

    def reject(self, item_id: str, definition_name: str, detail: str, /) -> ConnectorItemOutcome:
        """Record one provider item that cannot satisfy its Source Definition."""

        return self._record_provider_outcome(
            item_id,
            definition_name,
            ConnectorSubmissionStatus.REJECTED,
            detail,
        )

    def fail(self, item_id: str, definition_name: str, detail: str, /) -> ConnectorItemOutcome:
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
    ) -> ConnectorItemOutcome:
        _require_trimmed("detail", detail)
        if status not in {ConnectorSubmissionStatus.REJECTED, ConnectorSubmissionStatus.FAILED}:
            raise InvalidConnectorRunError("provider-outcome", "must be rejected or failed")
        self._claim_item(item_id, definition_name)
        outcome = ConnectorItemOutcome(
            item_id=item_id,
            definition_name=definition_name,
            status=status,
            detail=detail,
        )
        self._outcomes.append(outcome)
        return outcome

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
        source_definitions = validate_connector(connector, binding)
        previous = await self._checkpoints.load(binding)
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


def validate_connector(
    connector: Connector,
    binding: ConnectorBinding,
) -> frozenset[str]:
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
    if not callable(getattr(connector, "run", None)):
        raise InvalidConnectorError("run", "must be callable")
    return source_definitions


def _require_trimmed(field: str, value: object) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidConnectorRunError(field, "must be a non-empty trimmed string")
