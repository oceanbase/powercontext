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

"""Validate a conditional replace that restores one historical revision."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.profile.models import ProfileContent, ProfileWriteContent
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.artifacts.skill.package import build_instruction_skill_package
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent, TopicMemoryProjection
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.family_management import SkillManagementWriter
from powercontext.builtin.records import ArtifactWrite, InvalidBaseAccessRequestError

ROLLBACK_FAMILIES = frozenset({"profile", "prompt", "experience", "skill", "handoff", "topic-memory"})
_REASON_MAX = 1024


def merged_source_revision(family: str, write: ArtifactWrite, command: BaseModel) -> int | None:
    """Resolve the rollback source from the request and, for Profile, its content field."""

    requested = write.restored_from_revision
    embedded = command.restored_from_revision if isinstance(command, ProfileWriteContent) else None
    if requested is not None and embedded is not None and requested != embedded:
        raise InvalidBaseAccessRequestError(
            "restored_from_revision",
            "does not match content.restored_from_revision",
        )
    source = requested if requested is not None else embedded
    if family == "memory" and (source is not None or write.reason is not None):
        raise InvalidBaseAccessRequestError("restored_from_revision", "is not supported for memory")
    if family not in ROLLBACK_FAMILIES and (source is not None or write.reason is not None):
        raise InvalidBaseAccessRequestError("restored_from_revision", "is not supported")
    if source is None and write.reason is None:
        return None
    if source is None or write.reason is None:
        raise InvalidBaseAccessRequestError("reason", "is required when restoring a revision")
    normalize_reason(write.reason)
    return source


def normalize_reason(value: str) -> str:
    if len(value) > _REASON_MAX:
        raise InvalidBaseAccessRequestError("reason", "must be at most 1024 characters")
    stripped = value.strip()
    if not stripped:
        raise InvalidBaseAccessRequestError("reason", "must not be blank")
    return stripped


def prepare_profile_command(command: BaseModel, source: int | None) -> BaseModel:
    if not isinstance(command, ProfileWriteContent):
        return command
    if command.restored_from_revision == source:
        return command
    return command.model_copy(update={"restored_from_revision": source})


async def require_matching_source(
    artifacts: ArtifactRepository,
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    artifact_id: str,
    current: Artifact[Any],
    command: BaseModel,
    source_revision: int,
    writer: object,
) -> None:
    if source_revision == current.revision:
        raise InvalidBaseAccessRequestError("restored_from_revision", "is the current head")
    try:
        historical = await artifacts.get(
            connection,
            scope_id,
            ArtifactRef(family=family, artifact_id=artifact_id, revision=source_revision),
        )
    except RepositoryNotFoundError:
        raise InvalidBaseAccessRequestError("restored_from_revision", "does not exist") from None
    submitted = await _submitted_body(writer, connection, scope_id, family, command)
    if submitted != _stored_body(family, historical.content):
        raise InvalidBaseAccessRequestError("content", "does not match the restored revision")
    if submitted == _stored_body(family, current.content):
        raise InvalidBaseAccessRequestError("content", "matches the current head")


async def _submitted_body(
    writer: object,
    connection: AsyncConnection,
    scope_id: str,
    family: str,
    command: BaseModel,
) -> object:
    if family == "skill" and isinstance(command, SkillContent) and isinstance(writer, SkillManagementWriter):
        if command.package is None:
            command = build_instruction_skill_package(command).as_skill_content()
        else:
            command = await writer.canonical_content(connection, scope_id, command)
    return _stored_body(family, command)


def _stored_body(family: str, content: BaseModel) -> object:
    if family == "profile":
        if isinstance(content, ProfileContent | ProfileWriteContent):
            return content.content
        raise InvalidBaseAccessRequestError("content", "does not match the profile model")
    if family == "topic-memory":
        topic = content.content if isinstance(content, TopicMemoryProjection) else content
        if not isinstance(topic, TopicMemoryContent):
            raise InvalidBaseAccessRequestError("content", "does not match the topic memory model")
        return (topic.title, topic.summary, topic.detail)
    if family == "handoff":
        return content.model_dump(mode="json", by_alias=True, exclude={"generation"})
    return content.model_dump(mode="json", by_alias=True)
