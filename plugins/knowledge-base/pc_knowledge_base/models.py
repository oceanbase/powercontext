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

"""Plugin-owned request contracts and deterministic identities."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(*parts):
    return hashlib.sha256(canonical(parts).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpaceCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SpaceUpdate(Model):
    title: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)


class DefaultUpdate(Model):
    space_scope_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class KBCreate(SpaceCreate):
    space_scope_id: str = Field(min_length=1, max_length=64)
    summary: str = Field(default="Knowledge-base documents", min_length=1, max_length=1000)


class Chunking(Model):
    max_chars: int = Field(default=360, ge=80, le=12000)
    overlap: int = Field(default=40, ge=0, le=1000)


class Settings(Model):
    version: int = Field(ge=1)
    chunking: Chunking


class ImportFiles(Model):
    root: str = Field(min_length=1, max_length=4096)
    paths: list[str] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=128)


class Reprocess(Model):
    idempotency_key: str = Field(min_length=1, max_length=128)
    manual_edits_policy: Literal["reject", "replace"] = "reject"


class Retrieve(Model):
    query: str = Field(min_length=1, max_length=2000)
    space_scope_id: str | None = Field(default=None, min_length=1, max_length=64)
    scope_ids: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(default=10, ge=1, le=50)
    mode: Literal["hybrid", "fts", "vector"] = "hybrid"


class BatchChunks(Model):
    chunk_ids: list[str] = Field(min_length=1, max_length=100)


class ChunkEdit(Model):
    operation: Literal["edit_chunk", "remove_chunk"]
    chunk_id: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, min_length=1, max_length=12000)


class Evidence(Model):
    key: str
    payload: dict[str, object]
