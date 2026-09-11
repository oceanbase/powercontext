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

"""Family-owned adapters behind the common Artifact read APIs."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any, cast

import rfc8785
from pydantic import JsonValue, TypeAdapter, ValidationError
from typing_extensions import override

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemoryBrowseCursor, TopicMemoryCurrentItem
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.records import (
    ArtifactCollectionItem,
    ArtifactListReader,
    ArtifactRecordPage,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
)
from powercontext.builtin.tags import TagFilter

_JSON_VALUE = TypeAdapter(JsonValue)


class TopicMemoryArtifactListReader(ArtifactListReader):
    """Adapt published Topic Memory browse rows to the common Artifact page."""

    family = TopicMemory.family

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        topics: TopicMemoryRepository,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._topics = topics

    @override
    async def query(
        self,
        scope_id: str,
        /,
        *,
        limit: int,
        cursor: str | None,
        tag_filter: TagFilter | None,
        cursor_codec: SignedCursorCodec,
    ) -> ArtifactRecordPage:
        if tag_filter is not None:
            raise InvalidBaseAccessRequestError("tag", "is not supported for the topic-memory family")
        expected_cursor: Mapping[str, JsonValue] = {
            "version": 1,
            "endpoint": "list_artifacts",
            "scope_id": scope_id,
            "family": self.family,
            "order": "published_at:desc,artifact_id:asc,revision:desc",
        }
        after_text = cursor_codec.after_text(cursor, expected_cursor)
        after = _decode_after(after_text)

        async with self._database.transaction() as connection:
            browsed = await self._topics.browse_current(connection, scope_id, limit=limit, after=after)
            has_more = False
            if browsed:
                has_more = (
                    bool(
                        await self._topics.browse_current(
                            connection,
                            scope_id,
                            limit=1,
                            after=_browse_cursor(browsed[-1]),
                        )
                    )
                    if len(browsed) == limit
                    else False
                )
            artifacts = await self._artifacts.get_many(
                connection,
                scope_id,
                tuple(item.artifact_ref for item in browsed),
            )
            items = tuple(
                _collection_item(scope_id, artifact, item) for artifact, item in zip(artifacts, browsed, strict=True)
            )

        next_cursor = (
            cursor_codec.encode(expected_cursor, _browse_cursor(items[-1]).model_dump_json())
            if has_more and items
            else None
        )
        return ArtifactRecordPage(items=items, next_cursor=next_cursor)


def _decode_after(value: str) -> TopicMemoryBrowseCursor | None:
    if not value:
        return None
    try:
        return TopicMemoryBrowseCursor.model_validate_json(value, strict=True)
    except ValidationError:
        raise InvalidCursorError from None


def _browse_cursor(value: TopicMemoryCurrentItem | ArtifactCollectionItem) -> TopicMemoryBrowseCursor:
    if isinstance(value, TopicMemoryCurrentItem):
        artifact_ref = value.artifact_ref
        published_at = value.published_at
    else:
        if value.published_at is None:
            raise InvalidCursorError
        artifact_ref = ArtifactRef(family=value.family, artifact_id=value.artifact_id, revision=value.revision)
        published_at = value.published_at
    return TopicMemoryBrowseCursor(
        published_at=published_at,
        artifact_id=artifact_ref.artifact_id,
        revision=artifact_ref.revision,
    )


def _collection_item(scope_id: str, artifact: Any, browse: TopicMemoryCurrentItem) -> ArtifactCollectionItem:
    content = cast(dict[str, JsonValue], artifact.content.model_dump(mode="json", by_alias=True))
    return ArtifactCollectionItem(
        scope_id=scope_id,
        family=artifact.family,
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        sources=artifact.lineage.sources,
        artifacts=artifact.lineage.artifacts,
        content_digest=_content_digest(content),
        title=browse.title,
        summary=browse.summary,
        published_at=browse.published_at,
        source_count=browse.source_count,
    )


def _content_digest(value: JsonValue) -> str:
    validated = _JSON_VALUE.validate_python(value, strict=True)
    return f"sha256:{sha256(rfc8785.dumps(cast(Any, validated))).hexdigest()}"


__all__ = ["TopicMemoryArtifactListReader"]
