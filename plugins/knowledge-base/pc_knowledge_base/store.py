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

"""Four domain tables, immutable search projections, and portable CAS operations."""

from __future__ import annotations

import struct
import time

import jieba
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import IntegrityError

from pc_knowledge_base.models import canonical, identity

metadata = MetaData()
body_type = Text().with_variant(LONGTEXT(), "mysql").with_variant(LONGTEXT(), "oceanbase")
spaces = Table(
    "pc_knowledge_base_spaces",
    metadata,
    Column("space_scope_id", String(64), primary_key=True),
    Column("title", String(200), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("personal_owner_id", String(256, collation="utf8mb4_bin").with_variant(String(256), "sqlite"), unique=True),
    Column("created_by", String(256), nullable=False),
    Column("request_digest", String(64), nullable=False),
    Column("version", Integer, nullable=False),
)
defaults = Table(
    "pc_knowledge_base_space_defaults",
    metadata,
    Column("principal_id", String(256, collation="utf8mb4_bin").with_variant(String(256), "sqlite"), primary_key=True),
    Column("space_scope_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
)
resources = Table(
    "pc_knowledge_base_resources",
    metadata,
    Column("scope_id", String(128), primary_key=True),
    Column("resource_kind", String(16), primary_key=True),
    Column("resource_key", String(64), primary_key=True),
    Column("created_at_ms", BigInteger, nullable=False),
    Column("space_scope_id", String(64), index=True),
    Column("version", Integer, nullable=False),
    Column("value", JSON, nullable=False),
    Index("kb_resources_recent", "scope_id", "resource_kind", "created_at_ms"),
)
chunks = Table(
    "pc_knowledge_base_chunks",
    metadata,
    Column("row_id", String(64), primary_key=True),
    Column("scope_id", String(128), nullable=False, index=True),
    Column("document_id", String(64), nullable=False),
    Column("result_id", String(64), nullable=False),
    Column("chunk_id", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("text", body_type, nullable=False),
    Column("tokens", body_type, nullable=False),
    Column("details", JSON, nullable=False),
    Column("embedding", LargeBinary, nullable=False),
    UniqueConstraint("scope_id", "document_id", "result_id", "chunk_id"),
    UniqueConstraint("scope_id", "document_id", "result_id", "ordinal"),
)


def tokens(value):
    return " ".join(word.lower() for word in jieba.cut(value) if any(c.isalnum() for c in word))


class Store:
    def __init__(self, storage, dimension):
        self.engine = storage.engine()
        self.backend = storage.backend
        self.dimension = dimension
        if self.backend == "sqlite":
            import sqlite_vec

            @event.listens_for(self.engine.sync_engine, "connect")
            def load_vector(connection, record):
                connection.run_async(lambda c: c.enable_load_extension(True))
                connection.run_async(lambda c: c.load_extension(sqlite_vec.loadable_path()))
                connection.run_async(lambda c: c.enable_load_extension(False))
                cursor = connection.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

    async def initialize(self):
        async with self.engine.begin() as c:
            await c.run_sync(metadata.create_all)
            if self.backend == "sqlite":
                await c.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(row_id UNINDEXED, tokens)"))
            else:
                # Derived native projections; they never replace the authoritative chunk rows.
                await c.execute(
                    text(f"""CREATE TABLE IF NOT EXISTS kb_search (
                    row_id VARCHAR(64) PRIMARY KEY, scope_id VARCHAR(128) NOT NULL,
                    tokens LONGTEXT NOT NULL, embedding VECTOR({self.dimension}),
                    FULLTEXT INDEX kb_tokens(tokens), KEY kb_scope(scope_id))""")
                )

    async def one(self, table, **keys):
        async with self.engine.connect() as c:
            row = (await c.execute(select(table).filter_by(**keys))).mappings().first()
            return None if row is None else dict(row)

    async def rows(self, table, **keys):
        async with self.engine.connect() as c:
            return [dict(row) for row in (await c.execute(select(table).filter_by(**keys))).mappings()]

    async def add(self, table, values, keys):
        try:
            async with self.engine.begin() as c:
                await c.execute(insert(table).values(**values))
            return values  # noqa: TRY300
        except IntegrityError:
            existing = await self.one(table, **keys)
            if existing is None:
                raise
            return existing

    async def cas(self, table, keys, version, values):
        async with self.engine.begin() as c:
            changed = await c.execute(
                update(table).filter_by(**keys, version=version).values(**values, version=version + 1)
            )
            return changed.rowcount == 1

    async def resource(self, scope, kind, key):
        return await self.one(resources, scope_id=scope, resource_kind=kind, resource_key=key)

    async def add_resource(self, scope, kind, key, value, space=None):
        keys = {"scope_id": scope, "resource_kind": kind, "resource_key": key}
        return await self.add(
            resources,
            dict(**keys, space_scope_id=space, version=1, value=value, created_at_ms=time.time_ns() // 1_000_000),
            keys,
        )

    async def recent_requests(self, scope, limit=50):
        statement = (
            select(resources)
            .where(resources.c.scope_id == scope, resources.c.resource_kind == "request")
            .order_by(resources.c.created_at_ms.desc(), resources.c.resource_key.desc())
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            return [dict(row) for row in (await connection.execute(statement)).mappings()]

    async def observation(self, scope, key, **fields):
        for _ in range(10):
            row = await self.resource(scope, "request", key)
            if await self.cas(
                resources,
                {k: row[k] for k in ("scope_id", "resource_kind", "resource_key")},
                row["version"],
                {"value": {**row["value"], **fields}},
            ):
                return
        raise RuntimeError("processing observation changed too often")  # noqa: TRY003

    async def prepare(self, scope, document, result, blocks, vectors):
        # A result is either entirely durable (including both indexes), or entirely absent.
        existing = await self.rows(chunks, scope_id=scope, document_id=document, result_id=result)
        if existing:
            if len(existing) != len(blocks):
                raise RuntimeError("incomplete immutable result")  # noqa: TRY003
            return
        try:
            async with self.engine.begin() as c:
                for ordinal, (block, vector) in enumerate(zip(blocks, vectors, strict=True)):
                    row_id = identity(scope, document, result, block["chunk_id"])
                    lexical = tokens(block["text"])
                    await c.execute(
                        insert(chunks).values(
                            row_id=row_id,
                            scope_id=scope,
                            document_id=document,
                            result_id=result,
                            chunk_id=block["chunk_id"],
                            ordinal=ordinal,
                            text=block["text"],
                            tokens=lexical,
                            details={k: v for k, v in block.items() if k != "text"},
                            embedding=struct.pack(f"<{len(vector)}f", *vector),
                        )
                    )
                    if self.backend == "sqlite":
                        await c.execute(
                            text("INSERT INTO kb_fts(row_id,tokens) VALUES(:id,:tokens)"),
                            {"id": row_id, "tokens": lexical},
                        )
                    else:
                        await c.execute(
                            text(
                                "INSERT INTO kb_search(row_id,scope_id,tokens,embedding) "
                                "VALUES(:id,:scope,:tokens,:vector)"
                            ),
                            {"id": row_id, "scope": scope, "tokens": lexical, "vector": canonical(vector)},
                        )
        except IntegrityError:
            # Concurrent deterministic preparations have identical content-addressed identities.
            if len(await self.rows(chunks, scope_id=scope, document_id=document, result_id=result)) != len(blocks):
                raise

    async def blocks(self, scope, document, result):
        values = await self.rows(chunks, scope_id=scope, document_id=document, result_id=result)
        return [{**row["details"], "text": row["text"]} for row in sorted(values, key=lambda r: r["ordinal"])]

    async def chunk_page(self, scope, document, result, *, offset=0, limit=50, ids=None):
        columns = (chunks.c.chunk_id, chunks.c.ordinal, chunks.c.details)
        if ids is not None:
            columns = (*columns, chunks.c.text)
        statement = (
            select(*columns)
            .where(chunks.c.scope_id == scope, chunks.c.document_id == document, chunks.c.result_id == result)
            .order_by(chunks.c.ordinal)
        )
        if ids is None:
            statement = statement.offset(offset).limit(limit)
        else:
            statement = statement.where(chunks.c.chunk_id.in_(ids))
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings()
            return [
                {**row["details"], "ordinal": row["ordinal"], **({"text": row["text"]} if ids is not None else {})}
                for row in rows
            ]

    async def candidates(self, scopes, query, vector, mode, limit):
        if not scopes:
            return []
        params = {f"s{i}": value for i, value in enumerate(scopes)}
        scope_sql = ",".join(":" + key for key in params)
        params["limit"] = limit
        ranks = {}
        async with self.engine.connect() as c:
            if mode != "vector" and tokens(query):
                if self.backend == "sqlite":
                    params["query"] = " OR ".join('"' + term.replace('"', '""') + '"' for term in tokens(query).split())
                    sql = f"SELECT c.row_id FROM kb_fts JOIN pc_knowledge_base_chunks c ON c.row_id=kb_fts.row_id WHERE kb_fts MATCH :query AND c.scope_id IN ({scope_sql}) ORDER BY bm25(kb_fts),c.row_id LIMIT :limit"  # noqa: S608
                else:
                    params["query"] = tokens(query)
                    sql = f"SELECT row_id FROM kb_search WHERE scope_id IN ({scope_sql}) AND MATCH(tokens) AGAINST(:query IN NATURAL LANGUAGE MODE) > 0 ORDER BY MATCH(tokens) AGAINST(:query IN NATURAL LANGUAGE MODE) DESC,row_id LIMIT :limit"  # noqa: S608
                for rank, row in enumerate(await c.execute(text(sql), params), 1):
                    ranks[row[0]] = 1 / (60 + rank)
            if mode != "fts":
                if self.backend == "sqlite":
                    params["vector"] = struct.pack(f"<{len(vector)}f", *vector)
                    sql = f"SELECT row_id FROM pc_knowledge_base_chunks WHERE scope_id IN ({scope_sql}) ORDER BY vec_distance_cosine(embedding,:vector),row_id LIMIT :limit"  # noqa: S608
                else:
                    params["vector"] = canonical(vector)
                    sql = f"SELECT row_id FROM kb_search WHERE scope_id IN ({scope_sql}) ORDER BY cosine_distance(embedding,:vector),row_id LIMIT :limit"  # noqa: S608
                for rank, row in enumerate(await c.execute(text(sql), params), 1):
                    ranks[row[0]] = ranks.get(row[0], 0) + 1 / (60 + rank)
            if not ranks:
                return []
            found = (await c.execute(select(chunks).where(chunks.c.row_id.in_(ranks)))).mappings()
            return sorted(
                [{**dict(row), "score": ranks[row["row_id"]]} for row in found],
                key=lambda r: (-r["score"], r["row_id"]),
            )
