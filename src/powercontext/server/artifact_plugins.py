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

"""Server-owned hot registration and retained reads for custom Artifact plugins."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, union, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from powercontext._logging import log_safely
from powercontext.builtin.artifact_plugin_validation import validate_plugin
from powercontext.builtin.dream.bindings import DREAM_BINDINGS
from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sources import SOURCE_PROCESSING_BINDINGS
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACT_FAMILY_SCHEMAS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    ARTIFACT_SEARCH_STATES_TABLE,
    ARTIFACT_VECTOR_INDEXES_TABLE,
    ARTIFACT_VECTOR_SPACES_TABLE,
    SCOPES_TABLE,
)
from powercontext.builtin.records import BaseValueConflictError, BaseValueNotFoundError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.artifact_plugins import (
    ArtifactPluginWorkerSpec,
    run_artifact_plugin_worker,
)
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingProvider
from powercontext.http._generated.models import (
    ArtifactPluginState,
    EnableAllArtifactPluginsResponse,
    EnableArtifactPluginResult,
    ReloadArtifactPluginRequest,
)
from powercontext.server.authz.profiles import ARTIFACT_FAMILY_PROFILES, register_custom_artifact_access_family

_STABLE_PHASES = frozenset({"enabled", "disabled", "unloaded", "failed"})
_STATES = ARTIFACT_PROCESSING_BINDING_STATES_TABLE
_STATE_COLUMNS = (
    _STATES.c.binding_name,
    _STATES.c.plugin_id,
    _STATES.c.plugin_family,
    _STATES.c.binding_generation,
    _STATES.c.desired_state,
    _STATES.c.phase,
    _STATES.c.last_error,
)
_SCHEMAS = ARTIFACT_FAMILY_SCHEMAS_TABLE
logger = logging.getLogger(__name__)


async def read_artifact_declarations(database: AsyncDatabase) -> list[dict[str, Any]]:
    async with database.transaction() as connection:
        return list(
            (
                await connection.execute(
                    select(_SCHEMAS.c.declaration).order_by(_SCHEMAS.c.family, _SCHEMAS.c.schema_version)
                )
            ).scalars()
        )


async def refresh_artifact_access_families(database: AsyncDatabase, families: set[str]) -> None:
    families = families - ARTIFACT_FAMILY_PROFILES.keys()
    if not families:
        return
    statement = select(_SCHEMAS.c.family).distinct().where(_SCHEMAS.c.family.in_(families))
    async with database.transaction() as connection:
        registered = (await connection.execute(statement)).scalars().all()
    for family in registered:
        register_custom_artifact_access_family(family)


class ArtifactPluginManager:
    """Own registrations and transitions; user code stays outside the Server process."""

    def __init__(
        self,
        database: AsyncDatabase,
        config: DatabaseConfig,
        supervisor: ArtifactProcessingSupervisors | None,
        security_builder: Callable[[], dict[str, Any] | None],
        on_supervisor: Callable[[ArtifactProcessingSupervisors], None],
        plugins_dir: Path | None = None,
        embedding_model=None,
        inference_config=None,
        runtime_config=None,
    ) -> None:
        self.database = database
        self.index_builder = ArtifactIndexBuilder(database, embedding_model)
        self.inference_config = inference_config
        self.runtime_config = runtime_config
        self.config = config
        self.supervisor = supervisor
        self.security_builder = security_builder
        self.on_supervisor = on_supervisor
        self.plugins_dir = None if plugins_dir is None else plugins_dir.expanduser().resolve()
        self._resources = AsyncExitStack()
        self._supervisor_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._index_locks: dict[str, asyncio.Lock] = {}
        self._index_task: asyncio.Task[None] | None = None
        self._index_failures: set[tuple[str, str]] = set()
        self._index_refresh_families: set[str] = set()
        self._space_failures: set[str] = set()

    async def __aenter__(self):
        async with self.database.transaction() as connection:
            families = (await connection.execute(select(_SCHEMAS.c.family).distinct())).scalars().all()
            rows = (await connection.execute(select(_STATES).where(_STATES.c.plugin_id.is_not(None)))).mappings().all()
            restored = []
            for persisted in rows:
                if persisted["phase"] in {"failed", "disabled", "unloaded"}:
                    continue
                row = dict(persisted)
                row["binding_generation"] += 1
                row["phase"] = {"enabled": "activating", "disabled": "stopping", "unloaded": "unloading"}[
                    row["desired_state"]
                ]
                await connection.execute(
                    update(_STATES)
                    .where(_STATES.c.binding_name == row["binding_name"])
                    .values(binding_generation=row["binding_generation"], phase=row["phase"])
                )
                restored.append(row)
        self._index_refresh_families.update(families)
        for row in restored:
            await self._transition(row)
        await self._discover()
        # Retained indexes recover in the background; encoding existing content
        # must not delay Server startup or availability of unrelated families.
        self._index_task = asyncio.create_task(self._maintain_search(), name="artifact-index-maintenance")
        return self

    async def __aexit__(self, *_):
        if self._index_task is not None:
            self._index_task.cancel()
            await asyncio.gather(self._index_task, return_exceptions=True)
        try:
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            await self._resources.aclose()

    async def _ensure_supervisor(self) -> ArtifactProcessingSupervisors:
        async with self._supervisor_lock:
            if self.supervisor is None:
                controller = await self._resources.enter_async_context(
                    ArtifactProcessingSupervisor(
                        database=self.database,
                        bindings=(),
                        lease_mode="oceanbase" if isinstance(self.config, OceanBaseConfig) else "single-process",
                    )
                )
                self.supervisor = ArtifactProcessingSupervisors((controller,))
                self.on_supervisor(self.supervisor)
            return self.supervisor

    async def _row(self, name: str, *, include_definition: bool = False) -> dict[str, Any]:
        columns = (*_STATE_COLUMNS, _STATES.c.plugin_spec) if include_definition else _STATE_COLUMNS
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(*columns).where(_STATES.c.binding_name == name, _STATES.c.plugin_id.is_not(None))
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise BaseValueNotFoundError("artifact_plugin", name)
        return dict(row)

    @staticmethod
    def _state(row: dict[str, Any]) -> ArtifactPluginState:
        return ArtifactPluginState.model_validate({
            "binding_name": row["binding_name"],
            "plugin_id": row["plugin_id"],
            "family": row["plugin_family"],
            "binding_generation": row["binding_generation"],
            "desired_state": row["desired_state"],
            "phase": row["phase"],
            "last_error": row["last_error"],
        })

    async def get(self, name: str) -> ArtifactPluginState:
        return self._state(await self._row(name))

    async def list_registrations(self) -> list[ArtifactPluginState]:
        async with self.database.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        select(*_STATE_COLUMNS).where(_STATES.c.plugin_id.is_not(None)).order_by(_STATES.c.binding_name)
                    )
                )
                .mappings()
                .all()
            )
        return [self._state(dict(row)) for row in rows]

    async def _discover(self) -> None:
        directory = self.plugins_dir
        if directory is None:
            return
        try:
            entries = await asyncio.to_thread(lambda: sorted(directory.iterdir()))
        except OSError as error:
            log_safely(
                logger, logging.WARNING, f"Artifact plugin discovery directory unavailable ({type(error).__name__})"
            )
            return
        known = {state.binding_name for state in await self.list_registrations()}
        for entry in entries:
            if entry.name in known or not await asyncio.to_thread(entry.is_dir):
                continue
            try:
                await self.reload(entry.name, ReloadArtifactPluginRequest(expected_generation=0))
            except Exception as error:
                log_safely(
                    logger,
                    logging.WARNING,
                    f"Artifact plugin discovery failed: {entry.name!r} ({type(error).__name__})",
                )

    async def reload(self, name: str, request: ReloadArtifactPluginRequest) -> ArtifactPluginState:
        if re.fullmatch(r"[a-z][a-z0-9-]{0,127}", name) is None:
            raise InvalidBaseAccessRequestError("binding_name", "must be a canonical binding name")
        if name in {*SOURCE_PROCESSING_BINDINGS, *DREAM_BINDINGS.values()}:
            raise InvalidBaseAccessRequestError("binding_name", "is reserved for a built-in processor")
        directory = self.plugins_dir
        if directory is None:
            raise InvalidBaseAccessRequestError("artifact_plugins_dir", "must be configured on the Server")
        try:
            path = await asyncio.to_thread(lambda: (directory / name).resolve(strict=True))
            is_directory = await asyncio.to_thread(path.is_dir)
        except (OSError, RuntimeError) as error:
            raise InvalidBaseAccessRequestError(
                "binding_name", "must identify a deployed plugin directory entry"
            ) from error
        if not is_directory:
            raise InvalidBaseAccessRequestError("binding_name", "must identify a deployed plugin directory entry")
        try:
            descriptor = await validate_plugin(str(path))
        except (ValueError, OSError, TimeoutError) as error:
            raise InvalidBaseAccessRequestError("plugin", "declaration or configuration validation failed") from error
        if descriptor["manifest"]["binding_name"] != name:
            raise InvalidBaseAccessRequestError(
                "binding_name", "must match plugin.yml, directory entry and request path"
            )
        definition = {"directory": str(path), **descriptor}
        return await self._accept(name, request.expected_generation, None, definition)

    async def enable_all(self) -> EnableAllArtifactPluginsResponse:
        # Freeze eligibility and generations before any per-plugin validation can yield.
        snapshot = await self.list_registrations()
        results = []
        for state in snapshot:
            outcome, error = "skipped", None
            current = state
            if state.phase == "disabled":
                try:
                    current = await self.change(
                        state.binding_name, state.binding_generation, "enabled", require_disabled=True
                    )
                    outcome = "accepted"
                except Exception as failure:
                    outcome = "failed"
                    error = (
                        "conflict"
                        if isinstance(failure, BaseValueConflictError)
                        else "invalid_plugin"
                        if isinstance(failure, InvalidBaseAccessRequestError)
                        else "unavailable"
                    )
                    current = await self.get(state.binding_name)
            results.append(
                EnableArtifactPluginResult.model_validate({
                    "binding_name": state.binding_name,
                    "outcome": outcome,
                    "status_url": f"/v1/admin/artifact-plugins/{state.binding_name}",
                    "state": current,
                    "error": error,
                })
            )
        return EnableAllArtifactPluginsResponse(items=results)

    async def change(
        self, name: str, expected: int, target: str, *, require_disabled: bool = False
    ) -> ArtifactPluginState:
        row = await self._row(name, include_definition=True)
        if (
            row["binding_generation"] != expected
            or row["phase"] not in _STABLE_PHASES
            or (require_disabled and row["phase"] != "disabled")
        ):
            raise BaseValueConflictError("artifact_plugin", name)
        definition = row["plugin_spec"]
        if target == "enabled":
            if definition is None or row["phase"] == "failed":
                raise InvalidBaseAccessRequestError(
                    "plugin", "an unloaded or failed plugin requires reload before enabling"
                )
            if isinstance(self.config, SQLiteConfig) and self.config.is_in_memory:
                raise InvalidBaseAccessRequestError(
                    "database", "Artifact plugin generation requires persistent storage"
                )
            try:
                descriptor = await validate_plugin(definition["directory"], definition["manifest"])
            except (ValueError, OSError, TimeoutError) as error:
                raise InvalidBaseAccessRequestError("plugin", "saved plugin validation failed") from error
            if descriptor != {"manifest": definition["manifest"], "declaration": definition["declaration"]}:
                raise InvalidBaseAccessRequestError("plugin", "changed code must be registered with reload")
        return await self._accept(name, expected, target, definition, require_disabled=require_disabled)

    async def _validate_identity(self, connection, name, old, definition):  # noqa: C901 - Validate durable ownership and immutable declarations together.
        declaration = None if definition is None else definition["declaration"]
        if declaration is None:
            if old is None:
                raise BaseValueNotFoundError("artifact_plugin", name)
            plugin_id, family = old["plugin_id"], old["plugin_family"]
        else:
            plugin_id, family = declaration["plugin_id"], declaration["family"]
        if old is not None and (old["plugin_id"], old["plugin_family"]) != (plugin_id, family):
            raise BaseValueConflictError("artifact_plugin_identity", name)
        owner = await connection.scalar(
            select(_STATES.c.binding_name)
            .where(_STATES.c.plugin_family == family, _STATES.c.binding_name != name)
            .limit(1)
        )
        if owner is not None:
            raise BaseValueConflictError("artifact_family", family)
        if declaration is not None:
            scope_ids = definition["manifest"]["scope_ids"]
            if scope_ids is not None:
                scopes = set(
                    (
                        await connection.execute(
                            select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.scope_id.in_(scope_ids))
                        )
                    ).scalars()
                )
                if scopes != set(scope_ids):
                    raise InvalidBaseAccessRequestError("scope_ids", "must identify existing Scopes")
            durable = {key: declaration[key] for key in ("family", "schema_version", "content_schema", "search_fields")}
            if "search" in declaration:
                durable["search"] = declaration["search"]
            from powercontext.builtin.persistence.artifact_indexes import register_definition, search_declaration

            search = search_declaration(durable)
            saved = (
                (await connection.execute(select(_SCHEMAS.c.declaration).where(_SCHEMAS.c.family == family)))
                .scalars()
                .all()
            )
            # Only query policy may change; persisted index identity survives unload and restart.
            query_policy = {
                "modes",
                "default_mode",
                "defaults",
                "candidate_limit",
                "rrf_k",
                "text_weight",
                "vector_weight",
            }
            fixed = None if search is None else search.model_dump(exclude=query_policy)
            for item in saved:
                previous_search = search_declaration(item)
                previous_fixed = None if previous_search is None else previous_search.model_dump(exclude=query_policy)
                if previous_fixed != fixed:
                    raise BaseValueConflictError("artifact_search_declaration", family)
            if (
                saved
                and search is not None
                and all(item["schema_version"] != durable["schema_version"] for item in saved)
            ):
                raise BaseValueConflictError("artifact_search_declaration", family)
            identity = (_SCHEMAS.c.family == family) & (_SCHEMAS.c.schema_version == durable["schema_version"])
            previous = await connection.scalar(select(_SCHEMAS.c.declaration).where(identity))
            if previous is None:
                await connection.execute(
                    insert(_SCHEMAS).values(
                        family=family, schema_version=durable["schema_version"], declaration=durable
                    )
                )
            elif previous["content_schema"] != durable["content_schema"]:
                raise BaseValueConflictError("artifact_schema", (family, durable["schema_version"]))
            elif previous != durable:
                await connection.execute(update(_SCHEMAS).where(identity).values(declaration=durable))
            if search is not None:
                for index in search.indexes:
                    try:
                        await register_definition(connection, family, durable["schema_version"], index)
                    except ValueError as error:
                        raise BaseValueConflictError(
                            "artifact_search_definition", (family, index.name, index.projection_version)
                        ) from error
        return plugin_id, family

    async def _accept(  # noqa: C901 - Registration CAS and lifecycle acceptance share one transaction.
        self,
        name: str,
        expected: int,
        target: str | None,
        definition: dict[str, Any] | None,
        *,
        require_disabled: bool = False,
    ) -> ArtifactPluginState:
        lock = self._locks.setdefault(name, asyncio.Lock())
        if lock.locked():
            raise BaseValueConflictError("artifact_plugin", name)
        async with lock:
            try:
                async with self.database.transaction() as connection:
                    # This is also the per-registration serialization point used by Worker commit guards.
                    await connection.execute(
                        update(_STATES)
                        .where(_STATES.c.binding_name == name)
                        .values(binding_generation=_STATES.c.binding_generation)
                    )
                    old = (
                        (await connection.execute(select(_STATES).where(_STATES.c.binding_name == name)))
                        .mappings()
                        .first()
                    )
                    generation = 0 if old is None else int(old["binding_generation"])
                    if (
                        generation != expected
                        or (old is not None and (old["plugin_id"] is None or old["phase"] not in _STABLE_PHASES))
                        or (require_disabled and (old is None or old["phase"] != "disabled"))
                    ):
                        raise BaseValueConflictError("artifact_plugin", name)
                    if target is None:
                        target = "enabled" if old is not None and old["phase"] == "enabled" else "disabled"
                    if target == "enabled":
                        self.security_builder()
                    if (
                        old is not None
                        and old["plugin_spec"] == definition
                        and old["desired_state"] == target
                        and old["phase"] == target
                    ):
                        return self._state(dict(old))
                    plugin_id, family = await self._validate_identity(connection, name, old, definition)
                    values = {
                        "plugin_id": plugin_id,
                        "plugin_family": family,
                        "plugin_spec": definition,
                        "binding_generation": generation + 1,
                        "desired_state": target,
                        "phase": {"enabled": "activating", "disabled": "stopping", "unloaded": "unloading"}[target],
                        "last_error": None,
                    }
                    if old is None and target == "disabled":
                        values["phase"] = "disabled"
                    if old is None:
                        await connection.execute(insert(_STATES).values(binding_name=name, **values))
                    else:
                        await connection.execute(update(_STATES).where(_STATES.c.binding_name == name).values(**values))
            except IntegrityError as error:
                raise BaseValueConflictError("artifact_plugin", name) from error
            register_custom_artifact_access_family(family)
            if old is None:
                self._index_refresh_families.add(family)
            row = {"binding_name": name, **values}
            if row["phase"] == target:
                return self._state(row)
            task = asyncio.create_task(self._transition(row), name=f"artifact-plugin-transition-{name}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return self._state(row)

    async def _transition(self, row: dict[str, Any]) -> None:
        name, generation = row["binding_name"], row["binding_generation"]
        try:
            definition = row["plugin_spec"]
            target = row["desired_state"]
            supervisor = await self._ensure_supervisor() if target == "enabled" else self.supervisor
            if supervisor is not None:
                await supervisor.replace_binding(name, None)
            if target == "enabled" and supervisor is not None:
                descriptor = await validate_plugin(definition["directory"], definition["manifest"])
                if descriptor != {"manifest": definition["manifest"], "declaration": definition["declaration"]}:
                    raise ValueError("plugin code differs from the accepted definition")  # noqa: TRY003, TRY301
                manifest, declaration = descriptor["manifest"], descriptor["declaration"]
                spec = ArtifactPluginWorkerSpec(
                    self.config,
                    name,
                    generation,
                    definition,
                    self.security_builder(),
                    self.inference_config,
                    self.runtime_config,
                )

                async def scopes(connection, candidates):
                    phase = await connection.scalar(
                        select(_STATES.c.phase).where(
                            _STATES.c.binding_name == name, _STATES.c.binding_generation == generation
                        )
                    )
                    if phase != "enabled":
                        return frozenset()
                    scope_ids = manifest["scope_ids"]
                    return frozenset(candidates if scope_ids is None else set(candidates).intersection(scope_ids))

                await supervisor.replace_binding(
                    name,
                    ArtifactProcessingBinding(
                        binding_name=name,
                        artifact_family=declaration["family"],
                        launcher=SpawnArtifactProcessingWorkerLauncher(partial(run_artifact_plugin_worker, spec)),
                        max_workers=manifest["processing"]["max_workers"],
                        worker_timeout_seconds=manifest["processing"]["timeout_seconds"],
                        automatic_processing_interval=timedelta(seconds=manifest["processing"]["interval_seconds"]),
                        pending_provider=SourceProcessingPendingProvider(self.database, name, declaration["family"]),
                        automatic_scope_filter=scopes,
                        invocation_scope_filter=scopes,
                    ),
                )
            async with self.database.transaction() as connection:
                await connection.execute(
                    update(_STATES)
                    .where(_STATES.c.binding_name == name, _STATES.c.binding_generation == generation)
                    .values(phase=target, plugin_spec=None if target == "unloaded" else definition, last_error=None)
                )
            if supervisor is not None:
                supervisor.wake(name)
        except Exception as error:
            # No plugin configuration, credentials or exception input values in management status.
            async with self.database.transaction() as connection:
                await connection.execute(
                    update(_STATES)
                    .where(_STATES.c.binding_name == name, _STATES.c.binding_generation == generation)
                    .values(phase="failed", last_error=type(error).__name__)
                )

    async def _maintain_search(self):
        states = ARTIFACT_SEARCH_STATES_TABLE
        while True:
            try:
                # Persisted unfinished declarations also cover registration by
                # another Server, including families with no content or Scope.
                async with self.database.transaction() as connection:
                    spaces = ARTIFACT_VECTOR_SPACES_TABLE
                    definitions = ARTIFACT_VECTOR_INDEXES_TABLE
                    unfinished = await connection.scalars(
                        select(definitions.c.family)
                        .join(spaces, definitions.c.space_id == spaces.c.space_id)
                        .where(spaces.c.provision_state == "declared")
                        .distinct()
                    )
                    self._index_refresh_families.update(unfinished)
                # Startup and initial registration discover related pairs needing initialization.
                for family in tuple(self._index_refresh_families):
                    self._index_refresh_families.discard(family)
                    try:
                        from powercontext.builtin.persistence.artifact_vector_space import prepare_family_spaces

                        await prepare_family_spaces(self.database, family)
                        self._space_failures.discard(family)
                        complete = await self._refresh_search(family)
                    except Exception:
                        self._index_refresh_families.add(family)
                        if family not in self._space_failures:
                            self._space_failures.add(family)
                            log_safely(logger, logging.WARNING, "Artifact search maintenance unavailable; retrying")
                        continue
                    if not complete:
                        self._index_refresh_families.add(family)
                async with self.database.transaction(consistent_snapshot=True) as connection:
                    pairs = (
                        await connection.execute(
                            select(states.c.scope_id, states.c.family).where(states.c.state != "ready")
                        )
                    ).all()
                for scope, family in pairs:
                    async with self._index_locks.setdefault(family, asyncio.Lock()):
                        complete = False
                        try:
                            from powercontext.builtin.persistence.artifact_vector_space import prepare_family_spaces

                            await prepare_family_spaces(self.database, family)
                            await self.index_builder.refresh(scope, family)
                            complete = await self.index_builder.build(scope, family)
                        except Exception:
                            self._index_failure(scope, family)
                        if complete:
                            self._index_failures.discard((scope, family))
            except SQLAlchemyError:
                log_safely(logger, logging.WARNING, "Artifact search maintenance unavailable; retrying")
            await asyncio.sleep(2)

    def _index_failure(self, scope, family):
        if (scope, family) not in self._index_failures:
            self._index_failures.add((scope, family))
            log_safely(
                logger, logging.WARNING, "Artifact search rebuild incomplete; retrying while search is unavailable"
            )

    async def _refresh_search(self, family):
        """Initialize missing targets on startup or first registration, without encoding."""
        async with self._index_locks.setdefault(family, asyncio.Lock()):
            async with self.database.transaction(consistent_snapshot=True) as connection:
                scopes = (
                    (
                        await connection.execute(
                            union(
                                select(ARTIFACT_HEADS_TABLE.c.scope_id).where(ARTIFACT_HEADS_TABLE.c.family == family),
                                select(ARTIFACT_SEARCH_STATES_TABLE.c.scope_id).where(
                                    ARTIFACT_SEARCH_STATES_TABLE.c.family == family
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            complete = True
            for scope in scopes:
                try:
                    await self.index_builder.refresh(scope, family)
                except Exception:
                    complete = False
                    self._index_failure(scope, family)
            return complete
