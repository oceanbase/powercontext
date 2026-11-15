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

"""Space membership, import acceptance and document publication invariants."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import NoReturn

from pc_knowledge_base.content import ContentError, Snapshots, parse, relative_path
from pc_knowledge_base.embedding import Embedding
from pc_knowledge_base.models import ChunkEdit, Chunking, Retrieve, identity
from pc_knowledge_base.sources import FileEvidence, ProcessEvidence
from pc_knowledge_base.store import Store, defaults, resources, spaces
from powercontext.plugins import AccessDenied, ApiError, InvalidContent, Rendering, SearchHit, SearchResult


def fail(message: str, status: int = 409) -> NoReturn:
    raise ApiError(status, "knowledge_base_error", message)


def query(request, key, default=None):
    return request.query.get(key, [default])[0]


def integer(value, name, minimum, maximum):
    try:
        result = int(value)
    except (ValueError, TypeError):
        fail(f"{name} must be an integer", 422)
    if not minimum <= result <= maximum:
        fail(f"{name} must be between {minimum} and {maximum}", 422)
    return result


class KnowledgeBase:
    def __init__(self, ctx, store, snapshots, embedding):
        self.ctx, self.store, self.snapshots, self.embedding = ctx, store, snapshots, embedding

    async def visible_space(self, space):
        row = await self.store.one(spaces, space_scope_id=space)
        if row is None:
            fail("Space not found", 404)
        if row["personal_owner_id"] == self.ctx.caller.principal_id or await self.ctx.allowed("server.admin"):
            return row
        for kb in await self.store.rows(resources, resource_kind="scope", space_scope_id=space):
            if await self.ctx.allowed("scope.read", kb["scope_id"]):
                return row
        fail("Space not found", 404)

    async def default(self):
        principal = self.ctx.caller.principal_id
        current = await self.store.one(defaults, principal_id=principal)
        if current is None:
            space = identity("personal-space-v1", principal)
            await self.store.add(
                spaces,
                {
                    "space_scope_id": space,
                    "title": "Personal space",
                    "kind": "personal",
                    "personal_owner_id": principal,
                    "created_by": principal,
                    "request_digest": identity(principal),
                    "version": 1,
                },
                {"space_scope_id": space},
            )
            current = await self.store.add(
                defaults,
                {"principal_id": principal, "space_scope_id": space, "version": 1},
                {"principal_id": principal},
            )
        # An inaccessible selected space is an error, never a fallback to another search universe.
        await self.visible_space(current["space_scope_id"])
        return current

    async def default_get(self, request):
        return await self.default()

    async def default_set(self, request):
        await self.visible_space(request.body.space_scope_id)
        principal = self.ctx.caller.principal_id
        if not await self.store.cas(
            defaults, {"principal_id": principal}, request.body.version, {"space_scope_id": request.body.space_scope_id}
        ):
            fail("Default space changed; refresh before retrying")
        return await self.default()

    async def spaces_list(self, request):
        await self.default()
        result = []
        for row in await self.store.rows(spaces):
            try:
                await self.visible_space(row["space_scope_id"])
            except ApiError as error:
                if error.status == 404:
                    continue
                raise
            result.append(row)
        return {"items": result}

    async def spaces_create(self, request):
        await self.ctx.require("server.admin")
        body = request.body
        key = identity("space-create-v1", self.ctx.caller.principal_id, body.idempotency_key)
        value = {
            "space_scope_id": key,
            "title": body.title,
            "kind": "team",
            "personal_owner_id": None,
            "created_by": self.ctx.caller.principal_id,
            "request_digest": identity(body.model_dump()),
            "version": 1,
        }
        row = await self.store.add(spaces, value, {"space_scope_id": key})
        if row["request_digest"] != value["request_digest"]:
            fail("Creation key was already used with different parameters")
        return row

    async def spaces_update(self, request):
        await self.ctx.require("server.admin")
        key = request.path_params["space_id"]
        await self.visible_space(key)
        if not await self.store.cas(
            spaces, {"space_scope_id": key}, request.body.version, {"title": request.body.title}
        ):
            fail("Space changed; refresh before retrying")
        return await self.visible_space(key)

    async def kb_create(self, request):
        await self.ctx.require("server.admin")
        body = request.body
        await self.visible_space(body.space_scope_id)
        key = identity("kb-create-v1", self.ctx.caller.principal_id, body.space_scope_id, body.idempotency_key)
        scope = await self.ctx.scopes.create(title=body.title, summary=body.summary, idempotency_key=key)
        value = {
            "title": body.title,
            "summary": body.summary,
            "creation_digest": identity(body.model_dump()),
            "chunking": Chunking().model_dump(),
            "embedding_profile": self.embedding.profile,
        }
        row = await self.store.add_resource(scope.scope_id, "scope", "settings", value, body.space_scope_id)
        if row["space_scope_id"] != body.space_scope_id or row["value"]["creation_digest"] != value["creation_digest"]:
            fail("Creation key was already used with different parameters")
        return row

    async def kb_list(self, request):
        space = query(request, "space_scope_id") or (await self.default())["space_scope_id"]
        await self.visible_space(space)
        rows = await self.store.rows(resources, resource_kind="scope", space_scope_id=space)
        return {
            "space_scope_id": space,
            "items": [row for row in rows if await self.ctx.allowed("scope.read", row["scope_id"])],
        }

    async def settings(self, scope):
        row = await self.store.resource(scope, "scope", "settings")
        if row is None:
            fail("Knowledge base is not registered", 404)
        if row["value"]["embedding_profile"] != self.embedding.profile:
            fail("Embedding profile changed; restore the configured model. Online model switching is not supported")
        return row

    async def settings_update(self, request):
        await self.ctx.require("server.admin")
        row = await self.settings(request.scope_id)
        config = request.body.chunking.model_dump()
        if config["overlap"] >= config["max_chars"]:
            fail("Overlap must be smaller than chunk size", 422)
        keys = {"scope_id": request.scope_id, "resource_kind": "scope", "resource_key": "settings"}
        if not await self.store.cas(
            resources, keys, request.body.version, {"value": {**row["value"], "chunking": config}}
        ):
            fail("Settings changed; refresh before retrying")
        return await self.settings(request.scope_id)

    async def accept(
        self,
        scope,
        key,
        data,
        path,
        collection,
        origin,
        original_path=None,
        replace=False,
        force=False,
        command=None,
        *,
        base_revision=None,
    ):
        config = await self.settings(scope)
        path = relative_path(path)
        document = identity("document-v1", scope, collection, path)
        current = await self.ctx.reads(scope).get(document)
        if base_revision is None:
            base_revision = 0 if current is None else current.ref.revision
        request_key = identity("import-v1", self.ctx.caller.principal_id, scope, key)
        existing = await self.store.resource(scope, "request", request_key)
        if existing is None and current is not None:
            # Source contribution cannot authorize replacing another caller's document.
            # Replaying an accepted request retains its original authorization and fixed base.
            await self.ctx.require("artifact.write", scope, artifact_id=document)
        snapshot, digest = await asyncio.to_thread(self.snapshots.save, data)
        original = {
            "document_id": document,
            "snapshot": snapshot,
            "content_digest": digest,
            "relative_path": path,
            "collection_id": collection,
            "origin_kind": origin,
            "original_path": original_path,
        }
        pinned = {
            **original,
            "chunking": config["value"]["chunking"],
            "embedding_profile": self.embedding.profile,
            "base_revision": base_revision,
            "manual_edits_policy": "replace" if replace else "reject",
            "force_reprocess": force,
        }
        # Implicit settings are pinned on first acceptance; a retry keeps them after configuration changes.
        digest_request = (
            identity(command) if command is not None else identity(original, pinned["manual_edits_policy"], force)
        )
        if existing is None and current is not None and current.lifecycle_state != "active":
            fail("Document was forgotten and cannot be imported again")
        row = await self.store.add_resource(
            scope,
            "request",
            request_key,
            {"pinned": pinned, "request_digest": digest_request, "command": command, "state": "pending"},
        )
        if row["value"]["request_digest"] != digest_request:
            fail("Import key was already used with different file bytes or explicit parameters")
        return await self.submit_accepted(scope, row)

    async def submit_accepted(self, scope, row):
        """Complete Source registration from immutable accepted input, including interrupted submissions."""
        request_key = row["resource_key"]
        pinned = row["value"]["pinned"]
        original = {
            key: pinned[key]
            for key in (
                "document_id",
                "snapshot",
                "content_digest",
                "relative_path",
                "collection_id",
                "origin_kind",
                "original_path",
            )
        }
        source = await self.ctx.sources.submit(
            scope, "knowledge-base-file", FileEvidence(key=identity(original), payload=original)
        )
        await self.store.observation(scope, request_key, original_source=source.model_dump(mode="json"))
        processing = await self.ctx.sources.submit(
            scope,
            "knowledge-base-process",
            ProcessEvidence(
                key=request_key, payload={"request_key": request_key, "original_source": source.model_dump(mode="json")}
            ),
        )
        await self.store.observation(
            scope,
            request_key,
            original_source=source.model_dump(mode="json"),
            request_source=processing.model_dump(mode="json"),
        )
        await self.store.add_resource(
            scope,
            "document",
            pinned["document_id"],
            {key: pinned[key] for key in ("relative_path", "collection_id", "origin_kind")},
        )
        return {
            "request_id": request_key,
            "document_id": pinned["document_id"],
            "accepted": True,
            "source": source.model_dump(mode="json"),
            "request_source": processing.model_dump(mode="json"),
        }

    async def imports(self, request):
        body = request.body
        items = []
        for path in body.paths:
            try:
                data, original_path, collection = await asyncio.to_thread(self.snapshots.local, body.root, path)
                item = await self.accept(
                    request.scope_id,
                    identity(body.idempotency_key, path),
                    data,
                    path,
                    collection,
                    "server_directory",
                    original_path,
                )
                items.append({"path": path, **item})
            except AccessDenied:
                items.append({"path": path, "accepted": False, "status": 403, "error": "Access denied"})
            except ApiError as error:
                if error.status not in {400, 404, 409, 422}:
                    raise
                items.append({"path": path, "accepted": False, "status": error.status, "error": error.message})
            except (ContentError, OSError) as error:
                items.append({"path": path, "accepted": False, "error": type(error).__name__})
        return {"items": items}

    async def upload(self, request):
        path = query(request, "relative_path")
        key = query(request, "idempotency_key")
        if not path or not key or len(key) > 128:
            fail("relative_path and idempotency_key are required", 422)
        try:
            return await self.accept(
                request.scope_id, key, request.body, path, identity("browser-upload-v1"), "browser_upload"
            )
        except ContentError as error:
            fail(str(error), 422)

    async def reprocess(self, request):
        await self.ctx.require("scope.read", request.scope_id)
        command = {
            "operation": "reprocess",
            "document_id": request.path_params["document_id"],
            "manual_edits_policy": request.body.manual_edits_policy,
        }
        key = identity("import-v1", self.ctx.caller.principal_id, request.scope_id, request.body.idempotency_key)
        existing = await self.store.resource(request.scope_id, "request", key)
        if existing is not None:
            if existing["value"].get("command") != command:
                fail("Request key was already used with different explicit parameters")
            return await self.submit_accepted(request.scope_id, existing)
        snapshot = await self.ctx.reads(request.scope_id).get(request.path_params["document_id"])
        if snapshot is None:
            fail("Document not found", 404)
        content = snapshot.content
        data = await asyncio.to_thread(self.snapshots.read, content["snapshot"], content["content_digest"])
        return await self.accept(
            request.scope_id,
            request.body.idempotency_key,
            data,
            content["relative_path"],
            content["collection_id"],
            content["origin_kind"],
            content.get("original_path"),
            replace=request.body.manual_edits_policy == "replace",
            force=True,
            command=command,
            # The original bytes and publication precondition must refer to the same revision.
            base_revision=snapshot.ref.revision,
        )

    async def retry_import(self, request):
        row = await self.store.resource(request.scope_id, "request", request.path_params["request_id"])
        if row is None:
            fail("Request not found", 404)
        content = row["value"]["pinned"]
        data = await asyncio.to_thread(self.snapshots.read, content["snapshot"], content["content_digest"])
        return await self.accept(
            request.scope_id,
            request.body.idempotency_key,
            data,
            content["relative_path"],
            content["collection_id"],
            content["origin_kind"],
            content.get("original_path"),
            replace=request.body.manual_edits_policy == "replace",
            force=True,
            command={
                "operation": "retry_import",
                "request_id": request.path_params["request_id"],
                "manual_edits_policy": request.body.manual_edits_policy,
            },
        )

    async def status(self, request):
        await self.settings(request.scope_id)
        key = request.path_params["request_id"]
        row = await self.store.resource(request.scope_id, "request", key)
        if row is None:
            fail("Request not found", 404)
        state = row["value"]
        document = state["pinned"]["document_id"]
        published = await self.ctx.reads(request.scope_id).get(document, revision=state["pinned"]["base_revision"] + 1)
        current = await self.ctx.reads(request.scope_id).get(document)
        completed = published is not None and published.content.get("applied_request_id") == key
        return {
            "request_id": key,
            "document_id": document,
            "state": "completed" if completed else state["state"],
            "error": None if completed else state.get("error"),
            "current_revision": None if current is None else current.ref.revision,
            "lifecycle_state": None if current is None else current.lifecycle_state,
        }

    async def documents(self, request):
        await self.settings(request.scope_id)
        page = await self.ctx.reads(request.scope_id).list(
            states=("active", "deprecated"),
            cursor=query(request, "cursor"),
            limit=integer(query(request, "limit", 50), "limit", 1, 100),
        )
        requests = await self.store.recent_requests(request.scope_id)
        return {
            "items": [item.model_dump(mode="json") for item in page.items],
            "next_cursor": page.next_cursor,
            "requests": [
                {
                    "request_id": row["resource_key"],
                    "document_id": row["value"]["pinned"]["document_id"],
                    "state": row["value"]["state"],
                    "error": row["value"].get("error"),
                }
                for row in requests
            ],
        }

    async def generate(self, request, writer):  # noqa: C901
        if await self.store.resource(request.scope_id, "scope", "settings") is None:
            async with writer.transaction():
                pass
            return
        await self.settings(request.scope_id)
        prepared = []
        for source in request.sources:
            key = source.projection["key"]
            row = await self.store.resource(request.scope_id, "request", key)
            if (
                row is None
                or source.projection["payload"].get("request_key") != key
                or source.projection["payload"].get("original_source") != row["value"].get("original_source")
            ):
                continue
            pinned = row["value"]["pinned"]
            document = pinned["document_id"]
            published = await writer.get(document, revision=pinned["base_revision"] + 1)
            if published is not None and published.content.get("applied_request_id") == key:
                await self.store.observation(request.scope_id, key, state="completed", error=None)
                continue
            current = await writer.get(document)
            try:
                if (0 if current is None else current.ref.revision) != pinned["base_revision"]:
                    raise ContentError("Document changed; submit a new processing request")  # noqa: TRY003, TRY301
                if current is not None and current.lifecycle_state != "active":
                    raise ContentError("Document was forgotten")  # noqa: TRY003, TRY301
                if pinned["embedding_profile"] != self.embedding.profile:
                    raise ContentError("Processing request uses a different embedding profile")  # noqa: TRY003, TRY301
                if (
                    current is not None
                    and not pinned.get("force_reprocess")
                    and current.content["content_digest"] == pinned["content_digest"]
                    and current.content["chunking"] == pinned["chunking"]
                ):
                    await self.store.observation(
                        request.scope_id, key, state="completed", error=None, unchanged_revision=current.ref.revision
                    )
                    continue
                if (
                    current is not None
                    and current.content.get("manually_modified")
                    and pinned["manual_edits_policy"] != "replace"
                ):
                    raise ContentError("Reprocessing would replace manual edits; explicitly choose replace")  # noqa: TRY003, TRY301
                await self.store.observation(request.scope_id, key, state="processing", error=None)
                data = await asyncio.to_thread(self.snapshots.read, pinned["snapshot"], pinned["content_digest"])
                blocks = await asyncio.to_thread(parse, data, pinned["relative_path"], pinned["chunking"])
                manifest = await self.prepare(
                    request.scope_id,
                    document,
                    {
                        **pinned,
                        "original_source": source.projection["payload"]["original_source"],
                        "applied_request_id": key,
                        "manually_modified": False,
                    },
                    blocks,
                )
                prepared.append((source, manifest, pinned))
            except ContentError as error:
                await self.store.observation(request.scope_id, key, state="failed", error=str(error))
        # No remote/model/index work holds a core transaction. The writer commits together with the cursor.
        skipped = []
        async with writer.transaction() as tx:
            for source, manifest, pinned in prepared:
                current = await tx.get(pinned["document_id"])
                if (0 if current is None else current.ref.revision) != pinned["base_revision"] or (
                    current is not None and current.lifecycle_state != "active"
                ):
                    # A synchronous edit/forget won the race. Its version remains authoritative.
                    skipped.append(source.projection["key"])
                    continue
                await tx.put(
                    pinned["document_id"],
                    content=manifest,
                    source_refs=[source.ref],
                    expected_revision=pinned["base_revision"],
                    expected_state_version=None if current is None else current.governance_generation,
                )
        for key in skipped:
            await self.store.observation(
                request.scope_id, key, state="failed", error="Document changed during processing"
            )
        # The outer executor still owns core commit; status reads reconcile from committed lineage.

    async def prepare(self, scope, document, manifest, blocks):
        result = identity("result-v1", document, self.embedding.profile, blocks)
        vectors = await self.embedding.embed([block["text"] for block in blocks])
        await self.store.prepare(scope, document, result, blocks, vectors)
        return {
            **manifest,
            "result_id": result,
            "chunk_count": len(blocks),
            "title": Path(manifest["relative_path"]).name,
            "embedding_profile": self.embedding.profile,
        }

    async def update_artifact(self, request, writer):
        await self.settings(request.scope_id)
        current = await writer.get(request.artifact_id)
        if current is None or current.lifecycle_state != "active":
            raise InvalidContent("Only active documents can be edited")  # noqa: TRY003
        try:
            edit = ChunkEdit.model_validate(request.content)
            blocks = await self.store.blocks(request.scope_id, request.artifact_id, current.content["result_id"])
            match = next((block for block in blocks if block["chunk_id"] == edit.chunk_id), None)
            if match is None:
                raise ContentError("Chunk not found in the current revision")  # noqa: TRY003
            if edit.operation == "remove_chunk":
                blocks.remove(match)
            else:
                if edit.text is None:
                    raise ContentError("edit_chunk requires text")  # noqa: TRY003
                match.update(text=edit.text, length=len(edit.text), manually_modified=True)
            manifest = await self.prepare(
                request.scope_id, request.artifact_id, {**current.content, "manually_modified": True}, blocks
            )
        except ValueError as error:
            raise InvalidContent(str(error)) from error
        async with writer.transaction() as tx:
            await tx.put(
                request.artifact_id,
                content=manifest,
                # Prior lineage-only Sources are bound to their exact revision. Keep the original
                # processing evidence; prior edit evidence remains in immutable Artifact history.
                source_refs=[ref for ref in current.lineage.sources if ref.source_type == "knowledge-base-process"],
                expected_revision=request.expected_revision,
                expected_state_version=current.governance_generation,
            )

    async def forget_artifact(self, request, writer):
        await self.settings(request.scope_id)
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.artifact_id, "deprecated")

    async def render_artifact(self, request):
        blocks = await self.store.blocks(
            request.scope_id, request.artifact.ref.artifact_id, request.artifact.content["result_id"]
        )
        return Rendering(text="\n\n".join(block["text"] for block in blocks))

    async def chunk_read(self, request):
        revision = integer(request.path_params["revision"], "revision", 1, 2**31 - 1)
        document = request.path_params["document_id"]
        current = await self.ctx.reads(request.scope_id).get(document, revision=revision)
        if current is None:
            fail("Document revision not found", 404)
        result_id = current.content["result_id"]
        chunk_id = request.path_params.get("chunk_id")
        if chunk_id is not None or request.body is not None:
            ids = [chunk_id] if chunk_id is not None else request.body.chunk_ids
            blocks = await self.store.chunk_page(request.scope_id, document, result_id, ids=ids)
            by_id = {b["chunk_id"]: b for b in blocks}
            if any(key not in by_id for key in ids):
                fail("Chunk not found", 404)
            return by_id[chunk_id] if chunk_id is not None else {"items": [by_id[key] for key in ids]}
        offset = integer(query(request, "offset", 0), "offset", 0, 2**31 - 1)
        limit = integer(query(request, "limit", 50), "limit", 1, 100)
        selected = await self.store.chunk_page(request.scope_id, document, result_id, offset=offset, limit=limit)
        total = current.content["chunk_count"]
        return {"items": selected, "total": total, "next_offset": offset + limit if offset + limit < total else None}

    async def retrieve(self, request):
        body = request.body
        if request.scope_id is not None:
            space = (await self.settings(request.scope_id))["space_scope_id"]
            if body.space_scope_id is not None and body.space_scope_id != space:
                fail("Knowledge base does not belong to the selected space", 422)
        else:
            space = body.space_scope_id or (await self.default())["space_scope_id"]
        await self.visible_space(space)
        all_rows = await self.store.rows(resources, resource_kind="scope", space_scope_id=space)
        available = {row["scope_id"] for row in all_rows}
        selected = body.scope_ids
        if request.scope_id is not None:
            selected = [request.scope_id]
        if selected is None:
            selected = [scope for scope in sorted(available) if await self.ctx.allowed("scope.read", scope)]
        else:
            selected = list(dict.fromkeys(selected))
            for scope in selected:
                if scope not in available:
                    fail("Selected knowledge base does not belong to this space", 422)
                await self.ctx.require("scope.read", scope)
        if len(selected) > 20:
            fail("Select at most 20 knowledge bases", 422)
        for scope in selected:
            await self.settings(scope)
        try:
            hits = await self.search(selected, body.query, body.mode, body.limit)
        except ContentError as error:
            fail(str(error), 422)
        return {"space_scope_id": space, "scope_ids": selected, "hits": hits}

    async def search(self, scopes, query_text, mode, limit):
        vector = [] if mode == "fts" else (await self.embedding.embed([query_text], query=True))[0]
        hits = []
        # Refill after filtering historical/orphan results; bound total work for this first release.
        for candidate_limit in (max(100, limit * 4), 1000, 5000):
            candidates = await self.store.candidates(scopes, query_text, vector, mode, candidate_limit)
            heads = {}
            hits = []
            for row in candidates:
                key = (row["scope_id"], row["document_id"])
                if key not in heads:
                    heads[key] = await self.ctx.reads(key[0]).get(key[1])
                head = heads[key]
                if (
                    head is None
                    or head.lifecycle_state != "active"
                    or head.content["result_id"] != row["result_id"]
                    or head.content["embedding_profile"] != self.embedding.profile
                ):
                    continue
                hits.append({
                    "scope_id": key[0],
                    "document": head.ref.model_dump(mode="json"),
                    "chunk_id": row["chunk_id"],
                    "text": row["text"],
                    "score": row["score"],
                    "locator": row["details"]["locator"],
                    "heading_path": row["details"]["heading_path"],
                    "manually_modified": row["details"]["manually_modified"],
                    "source": head.content["original_source"],
                })
                if len(hits) >= limit:
                    return hits
            if len(candidates) < candidate_limit:
                break
        return hits

    async def search_artifacts(self, request):
        unsupported = set(request.search) - {"query", "limit", "mode"}
        if unsupported:
            raise InvalidContent("Unsupported search fields: " + ", ".join(sorted(unsupported)))
        body = Retrieve.model_validate({k: v for k, v in request.search.items() if k in {"query", "limit", "mode"}})
        await self.settings(request.scope_id)
        try:
            results = await self.search([request.scope_id], body.query, body.mode, body.limit)
        except ContentError as error:
            raise InvalidContent(str(error)) from error
        return SearchResult(
            hits=tuple(
                SearchHit(
                    ref=row["document"],
                    text=row["text"],
                    score=row["score"],
                    locator={"chunk_id": row["chunk_id"], **row["locator"]},
                )
                for row in results
            )
        )


async def setup(ctx):
    root = os.environ.get("POWERCONTEXT_KB_DATA_DIR")
    if not root:
        raise RuntimeError("Set POWERCONTEXT_KB_DATA_DIR to a shared local directory for Server and Workers")  # noqa: TRY003
    embedding = Embedding(os.environ.get("POWERCONTEXT_KB_MODEL", "BAAI/bge-small-zh-v1.5"), str(Path(root) / "models"))
    store = Store(ctx.storage, embedding.dimension)
    await store.initialize()
    roots = os.environ.get("POWERCONTEXT_KB_IMPORT_ROOTS", "").split(os.pathsep)
    snapshots = await asyncio.to_thread(Snapshots, Path(root) / "snapshots", [p for p in roots if p])
    return KnowledgeBase(ctx, store, snapshots, embedding)
