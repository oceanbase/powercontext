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

"""Keyed remember_memory retries create one memory and replay its original result."""

from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.family_management import AtomicMemoryManagementWriter
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import BaseValueConflictError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.client import PowerContextClient
from powercontext.http import RememberMemoryRequest
from powercontext.server.authentication import AuthenticationResult, ProviderReadiness
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")


def _settings(database: Path, **options) -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
        **options,
    )


@asynccontextmanager
async def _open(database: Path):
    app = create_server_app(settings=_settings(database))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        yield client


async def _default_scope(client: httpx.AsyncClient) -> str:
    sdk = PowerContextClient("http://testserver", http_client=client, trust_transport_security=True)
    return (await sdk.get_default_scope()).scope_id


def _count(database: Path, sql: str, *parameters) -> int:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(sql, parameters).fetchone()[0]


def _memories(database: Path) -> int:
    return _count(database, "SELECT COUNT(DISTINCT artifact_id) FROM pc_artifacts WHERE family = 'atomic-memory'")


def _sources(database: Path) -> int:
    return _count(database, "SELECT COUNT(*) FROM pc_sources")


async def _remember(client: httpx.AsyncClient, scope_id: str, text: str, key: str | None = None, **fields):
    body = {"scope_id": scope_id, "kind": "preference", "text": text, **fields}
    if key is not None:
        body["idempotency_key"] = key
    return await client.post("/v1/memory/remember", json=body)


def test_keyed_retry_replays_original_creation(tmp_path: Path) -> None:
    database = tmp_path / "replay.db"

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            key = f"retry-{uuid4()}"
            first = await _remember(client, scope_id, "Cafe\N{COMBINING ACUTE ACCENT} order is oat milk", key)
            assert first.status_code == 200, first.text
            created = first.json()
            assert created["changed"] is True
            record = created["records"][0]
            assert record["artifact"]["revision"] == 1
            assert (record["state"], record["state_version"], record["merged_into_id"]) == ("active", 0, None)
            memories, sources = _memories(database), _sources(database)

            # Equivalent stored content replays: NFC, outer whitespace, the default description, and reason.
            retries: tuple[tuple[str, dict[str, str | None]], ...] = (
                ("Cafe\N{COMBINING ACUTE ACCENT} order is oat milk", {}),
                ("  Café order is oat milk \n", {}),
                ("Café order is oat milk", {"description": None}),
                ("Café order is oat milk", {"description": "Café order is oat milk"}),
                ("Café order is oat milk", {"reason": "retry after a lost response"}),
            )
            for text, retry in retries:
                replayed = await _remember(client, scope_id, text, key, **retry)
                assert replayed.status_code == 200, replayed.text
                assert replayed.json() == created
            sdk = PowerContextClient("http://testserver", http_client=client, trust_transport_security=True)
            through_sdk = await sdk.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope_id, kind="preference", text="Café order is oat milk", idempotency_key=key
                )
            )
            assert through_sdk.model_dump(mode="json") == created
            assert (_memories(database), _sources(database)) == (memories, sources)

            # Keys are independent of each other, and requests without a key keep creating.
            other = await _remember(client, scope_id, "Café order is oat milk", f"retry-{uuid4()}")
            assert other.json()["records"][0]["artifact"]["artifact_id"] != record["artifact"]["artifact_id"]
            for _ in range(2):
                unkeyed = await _remember(client, scope_id, "Café order is oat milk")
                assert unkeyed.status_code == 200, unkeyed.text
                # A keyed creation answers exactly as an unkeyed creation of the same content.
                unkeyed_record = unkeyed.json()["records"][0]
                assert {**unkeyed_record, "artifact": None} == {**record, "artifact": None}
                assert unkeyed_record["artifact"].keys() == record["artifact"].keys()
            assert _memories(database) == memories + 3

            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute("SELECT * FROM pc_atomic_memory_idempotency").fetchall()
            assert len(rows) == 2
            assert not any(key in str(value) for row in rows for value in row)

    asyncio.run(scenario())


def test_replay_returns_creation_state_after_the_memory_changes(tmp_path: Path) -> None:
    database = tmp_path / "state.db"

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            first = await _remember(client, scope_id, "Deploy on Tuesdays", "deploy-day")
            assert first.status_code == 200, first.text
            record = first.json()["records"][0]
            forgotten = await client.post(
                "/v1/atomic-memory/lifecycle",
                json={
                    "scope_id": scope_id,
                    "target": {"artifact": record["artifact"], "state_version": record["state_version"]},
                    "state": "forgotten",
                },
            )
            assert forgotten.status_code == 200, forgotten.text
            replayed = await _remember(client, scope_id, "Deploy on Tuesdays", "deploy-day")
            assert replayed.status_code == 200, replayed.text
            assert replayed.json() == first.json()
            assert _memories(database) == 1

    asyncio.run(scenario())


def test_reused_key_with_different_content_conflicts_without_writing(tmp_path: Path) -> None:
    database = tmp_path / "conflict.db"

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            first = await _remember(client, scope_id, "Use pnpm", "tooling")
            assert first.status_code == 200, first.text
            memories, sources = _memories(database), _sources(database)
            for text, fields in (
                ("Use npm", {}),
                ("Use pnpm", {"kind": "fact"}),
                ("Use pnpm", {"description": "Package manager choice"}),
            ):
                conflict = await _remember(client, scope_id, text, "tooling", **fields)
                assert conflict.status_code == 409, conflict.text
                error = conflict.json()["error"]
                assert error["code"] == "idempotency_conflict"
                assert error["details"] == {"kind": "memory_idempotency_key"}
            assert (_memories(database), _sources(database)) == (memories, sources)
            replayed = await _remember(client, scope_id, "Use pnpm", "tooling")
            assert replayed.json() == first.json()

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["", "has space", "tab\tkey", "é", "k" * 257])
def test_invalid_key_is_rejected_before_any_write(tmp_path: Path, key: str) -> None:
    database = tmp_path / "invalid.db"

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            before = _sources(database)
            rejected = await _remember(client, scope_id, "Never stored", key)
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["error"]["code"] == "invalid_request"
            assert (_memories(database), _sources(database)) == (0, before)

    asyncio.run(scenario())


def test_in_process_create_applies_the_same_key_rule() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Keys", summary="SDK keys", idempotency_key="keys"))
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            content = (AtomicMemoryContent(kind="preference", text="Use pnpm"),)
            for key in ("", "has space", "é", "k" * 257):
                with pytest.raises(InvalidBaseAccessRequestError):
                    await memory.create(content, idempotency_key=key)
            assert (await memory.list()).items == ()
            first = await memory.create(content, idempotency_key="sdk-1")
            assert await memory.create(content, idempotency_key="sdk-1") == first

    asyncio.run(scenario())


def test_in_process_keys_share_one_namespace_without_access_control() -> None:
    """A context that names a principal but carries no access service does not split the namespace."""
    from powercontext.artifacts.search import ArtifactSearchExecutionContext

    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Keys", summary="SDK keys", idempotency_key="keys"))
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            content = (AtomicMemoryContent(kind="preference", text="Use pnpm"),)
            named = ArtifactSearchExecutionContext(principal=PrincipalRef(type="user", id="alice"), trusted_local=True)

            first = await memory.create(content, idempotency_key="shared")
            assert await memory.create(content, idempotency_key="shared", context=named) == first
            assert len((await memory.list()).items) == 1
            with pytest.raises(BaseValueConflictError):
                other = (AtomicMemoryContent(kind="preference", text="Use npm"),)
                await memory.create(other, idempotency_key="shared", context=named)

    asyncio.run(scenario())


def test_keyed_request_keeps_expected_revision_rejection(tmp_path: Path) -> None:
    database = tmp_path / "expected.db"

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            unkeyed = await _remember(client, scope_id, "Never stored", expected_revision=1)
            keyed = await _remember(client, scope_id, "Never stored", "precondition", expected_revision=1)
            assert unkeyed.status_code != 200
            assert (keyed.status_code, keyed.json()["error"]["code"]) == (
                unkeyed.status_code,
                unkeyed.json()["error"]["code"],
            )
            assert _memories(database) == 0
            assert _count(database, "SELECT COUNT(*) FROM pc_atomic_memory_idempotency") == 0

    asyncio.run(scenario())


def test_expired_key_starts_a_new_creation_and_cleanup_is_bounded(tmp_path: Path) -> None:
    database = tmp_path / "expiry.db"

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            first = await _remember(client, scope_id, "Prefer short PRs", "expiring")
            assert first.status_code == 200, first.text
            live = await _remember(client, scope_id, "Prefer squash merges", "still-live")
            assert live.status_code == 200, live.text
            with closing(sqlite3.connect(database)) as connection:
                expiring = connection.execute(
                    "SELECT * FROM pc_atomic_memory_idempotency WHERE artifact_id = ?",
                    (first.json()["records"][0]["artifact"]["artifact_id"],),
                ).fetchone()
                connection.execute("UPDATE pc_atomic_memory_idempotency SET expires_at = '2000-01-01 00:00:00.000000'")
                connection.execute(
                    "UPDATE pc_atomic_memory_idempotency SET expires_at = '2999-01-01 00:00:00.000000' WHERE artifact_id = ?",
                    (live.json()["records"][0]["artifact"]["artifact_id"],),
                )
                for index in range(40):
                    connection.execute(
                        "INSERT INTO pc_atomic_memory_idempotency VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"{index:064x}", *expiring[1:7], "2000-01-01 00:00:00.000000", "2000-01-01 00:00:00.000000"),
                    )
                connection.commit()
            expired_before = _count(
                database, "SELECT COUNT(*) FROM pc_atomic_memory_idempotency WHERE expires_at < '2001-01-01 00:00:00'"
            )

            recreated = await _remember(client, scope_id, "Prefer short PRs", "expiring")
            assert recreated.status_code == 200, recreated.text
            recreated_id = recreated.json()["records"][0]["artifact"]["artifact_id"]
            assert recreated_id != first.json()["records"][0]["artifact"]["artifact_id"]
            assert (await _remember(client, scope_id, "Prefer short PRs", "expiring")).json() == recreated.json()
            assert (await _remember(client, scope_id, "Prefer squash merges", "still-live")).json() == live.json()

            expired_after = _count(
                database, "SELECT COUNT(*) FROM pc_atomic_memory_idempotency WHERE expires_at < '2001-01-01 00:00:00'"
            )
            assert expired_before == 41
            # The recreation replaced its own expired key, and its cleanup removed part of the
            # other backlog: a bounded batch, not a full scan.
            assert 0 < expired_after < expired_before - 1
            assert (
                _count(
                    database,
                    "SELECT COUNT(*) FROM pc_atomic_memory_idempotency WHERE expires_at > '2001-01-01 00:00:00'",
                )
                == 2
            )

    asyncio.run(scenario())


def test_concurrent_keyed_requests_create_exactly_once(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "race.db"
    original = AtomicMemoryManagementWriter.prepare_creates

    async def scenario() -> None:
        async with _open(database) as client:
            scope_id = await _default_scope(client)
            barrier = asyncio.Barrier(3)

            async def prepare_together(self, *args, **kwargs):
                # Every request has missed the lookup before any of them writes.
                await barrier.wait()
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(AtomicMemoryManagementWriter, "prepare_creates", prepare_together)
            responses = await asyncio.gather(*(_remember(client, scope_id, "Race once", "race") for _ in range(3)))
            assert [response.status_code for response in responses] == [200, 200, 200], [r.text for r in responses]
            assert responses[0].json() == responses[1].json() == responses[2].json()
            assert _memories(database) == 1
            assert _count(database, "SELECT COUNT(*) FROM pc_atomic_memory_idempotency") == 1

    asyncio.run(scenario())


def _remember_in_process(database: str, scope_id: str, text: str, barrier, results) -> None:
    async def run() -> None:
        original = AtomicMemoryManagementWriter.prepare_creates

        async def prepare_together(self, *args, **kwargs):
            await asyncio.to_thread(barrier.wait, 120)
            return await original(self, *args, **kwargs)

        AtomicMemoryManagementWriter.prepare_creates = prepare_together
        async with _open(Path(database)) as client:
            response = await _remember(client, scope_id, text, "cross-process")
            results.put((response.status_code, response.json()))

    asyncio.run(run())


@pytest.mark.parametrize("texts", [("Same body",) * 3, ("Body A", "Body B", "Body A")], ids=["same", "mixed"])
def test_keyed_requests_from_separate_processes_create_exactly_once(tmp_path: Path, texts) -> None:
    database = tmp_path / "processes.db"

    async def prepare() -> str:
        async with _open(database) as client:
            return await _default_scope(client)

    scope_id = asyncio.run(prepare())
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(texts))
    results = context.Queue()
    workers = [
        context.Process(target=_remember_in_process, args=(str(database), scope_id, text, barrier, results))
        for text in texts
    ]
    for worker in workers:
        worker.start()
    outcomes = [results.get(timeout=300) for _ in workers]
    for worker in workers:
        worker.join(timeout=60)
        assert worker.exitcode == 0
    assert _memories(database) == 1
    succeeded = [body for status, body in outcomes if status == 200]
    assert succeeded and all(body == succeeded[0] for body in succeeded)
    winner = succeeded[0]["records"][0]["text"]
    assert len(succeeded) == texts.count(winner)
    conflicts = [body for status, body in outcomes if status != 200]
    assert len(conflicts) == len(texts) - texts.count(winner)
    assert all(body["error"]["details"] == {"kind": "memory_idempotency_key"} for body in conflicts)


class _Authentication:
    async def authenticate(self, request):
        identity = request.headers.get("authorization", "Bearer admin").removeprefix("Bearer ")
        return AuthenticationResult(subject=ADMIN if identity == "admin" else PrincipalRef(type="user", id=identity))

    async def readiness(self):
        return ProviderReadiness(ready=True)


async def _grant(client: httpx.AsyncClient, scope_id: str, principal: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": "user", "id": principal},
            "resource": {"type": "scope", "scope_id": scope_id},
            "role": "scope.contributor",
            "idempotency_key": f"{principal}-contributor",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_access_controlled_keys_are_per_principal_and_replays_reauthorize(tmp_path: Path) -> None:
    database = tmp_path / "access.db"

    async def scenario() -> None:
        server_settings = _settings(
            database,
            runtime=RuntimeConfig(artifact_processing_families=()),
            access=AccessControlConfig(mode="enforced", deployment_id="idempotency"),
        )
        await asyncio.to_thread(prepare_server_database, server_settings)
        async with open_builtin_access_control(
            server_settings.database, bootstrap_administrators=(ADMIN,), deployment_id="idempotency"
        ) as access:
            app = create_server_app(
                settings=server_settings,
                authentication_provider=_Authentication(),
                access_control=access,
                scheduler_path=tmp_path / "scheduler.db",
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
            ):
                created = await client.post(
                    "/v1/scopes", json={"title": "Keys", "summary": "Idempotent writes", "idempotency_key": "keys"}
                )
                assert created.status_code == 201, created.text
                scope_id = created.json()["scope_id"]
                alice_grant = await _grant(client, scope_id, "alice")
                await _grant(client, scope_id, "bob")
                alice = {"Authorization": "Bearer alice"}
                bob = {"Authorization": "Bearer bob"}
                body = {"scope_id": scope_id, "kind": "note", "text": "Private plan", "idempotency_key": "shared"}

                first = await client.post("/v1/memory/remember", json=body, headers=alice)
                assert first.status_code == 200, first.text
                again = await client.post("/v1/memory/remember", json=body, headers=alice)
                assert again.json() == first.json()
                other = await client.post("/v1/memory/remember", json=body, headers=bob)
                assert other.status_code == 200, other.text
                assert other.json()["records"][0]["artifact"] != first.json()["records"][0]["artifact"]

                invalid = await client.post(
                    "/v1/memory/remember", json={**body, "idempotency_key": "bad key"}, headers=alice
                )
                assert invalid.status_code == 422
                assert invalid.json()["error"]["code"] in {"invalid_request", "invalid_access_request"}

                revoked = await client.post(
                    "/v1/access/bindings/revoke",
                    json={
                        "binding_id": alice_grant["binding_id"],
                        "expected_version": alice_grant["version"],
                        "idempotency_key": "revoke-alice",
                    },
                )
                assert revoked.status_code == 200, revoked.text
                denied = await client.post("/v1/memory/remember", json=body, headers=alice)
                assert denied.status_code == 403
                assert "Private plan" not in denied.text
                assert _memories(database) == 2

            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute("SELECT * FROM pc_atomic_memory_idempotency").fetchall()
            assert len(rows) == 2
            assert not any(
                secret in str(value) for row in rows for value in row for secret in ("shared", "alice", "bob")
            )

    asyncio.run(scenario())


def test_in_process_keyed_create_requires_current_scope_contribution(tmp_path: Path) -> None:
    """A revoked contributor learns neither a replay nor a conflict from an old key."""
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.builtin.authz import AccessAuditContext, AccessDeniedError

    database = tmp_path / "in-process-access.db"

    async def scenario() -> None:
        server_settings = _settings(
            database,
            runtime=RuntimeConfig(artifact_processing_families=()),
            access=AccessControlConfig(mode="enforced", deployment_id="idempotency"),
        )
        await asyncio.to_thread(prepare_server_database, server_settings)
        async with open_builtin_access_control(
            server_settings.database, bootstrap_administrators=(ADMIN,), deployment_id="idempotency"
        ) as access:
            app = create_server_app(
                settings=server_settings,
                authentication_provider=_Authentication(),
                access_control=access,
                scheduler_path=tmp_path / "scheduler.db",
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
            ):
                created = await client.post(
                    "/v1/scopes", json={"title": "Keys", "summary": "Idempotent writes", "idempotency_key": "keys"}
                )
                assert created.status_code == 201, created.text
                scope_id = created.json()["scope_id"]
                grant = await _grant(client, scope_id, "alice")
                memory = app.state.application.atomic_memory.for_scope(scope_id)
                context = ArtifactSearchExecutionContext(
                    principal=PrincipalRef(type="user", id="alice"),
                    access=access,
                    audit=AccessAuditContext(transport="http", operation="test"),
                )
                content = AtomicMemoryContent(kind="note", text="Private plan")

                first = await memory.create((content,), idempotency_key="in-process", context=context)
                replayed = await memory.create((content,), idempotency_key="in-process", context=context)
                assert replayed.records[0].artifact == first.records[0].artifact
                assert _memories(database) == 1

                revoked = await client.post(
                    "/v1/access/bindings/revoke",
                    json={
                        "binding_id": grant["binding_id"],
                        "expected_version": grant["version"],
                        "idempotency_key": "revoke-alice",
                    },
                )
                assert revoked.status_code == 200, revoked.text
                other = AtomicMemoryContent(kind="note", text="Different plan")
                for value, key in ((content, "in-process"), (other, "in-process"), (content, "unused")):
                    with pytest.raises(AccessDeniedError):
                        await memory.create((value,), idempotency_key=key, context=context)
                assert _memories(database) == 1
                assert _count(database, "SELECT COUNT(*) FROM pc_atomic_memory_idempotency") == 1

    asyncio.run(scenario())


def test_untrusted_in_process_context_cannot_probe_an_occupied_key() -> None:
    """An explicit context that is neither access-controlled nor trusted local is denied before any lookup."""
    from powercontext.artifacts.search import ArtifactSearchExecutionContext
    from powercontext.builtin.authz import AccessDeniedError

    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Keys", summary="SDK keys", idempotency_key="keys"))
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            content = (AtomicMemoryContent(kind="preference", text="Use pnpm"),)
            other = (AtomicMemoryContent(kind="preference", text="Use npm"),)
            await memory.create(content, idempotency_key="known")

            untrusted = ArtifactSearchExecutionContext()
            for value, key in ((content, None), (content, "unused"), (content, "known"), (other, "known")):
                with pytest.raises(AccessDeniedError):
                    await memory.create(value, idempotency_key=key, context=untrusted)
            assert len((await memory.list()).items) == 1

    asyncio.run(scenario())
