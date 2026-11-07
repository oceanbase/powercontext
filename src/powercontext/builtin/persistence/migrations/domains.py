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

"""Frozen domain transformations attached to the shared schema revision chain."""

from __future__ import annotations

import copy
import runpy
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Connection

from .models import MigrationError

Backend = Literal["sqlite", "seekdb", "oceanbase"]


class DomainStep(BaseModel):
    """Run after this revision's schema expansion and before advancing its version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    revision: str
    kind: Literal["data", "projection"]
    module: str
    resources: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    backends: tuple[Backend, ...] = Field(min_length=1)


class _Registrations(BaseModel):
    domain_steps: tuple[DomainStep, ...] = ()
    domain_readiness: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class DomainRegistry:
    """Plan and execute domain-owned steps without a separate task journal.

    Modules export preflight/apply/verify/validate(connection, *, configuration).
    preflight and validate are read-only. verify checks migration-copy invariants;
    validate checks live invariants and must not compare mutable data to archives.
    apply must be idempotent and must not commit, change schema, or open connections.
    The backend runner owns transactions, locking, recovery and version advancement.
    """

    def __init__(
        self,
        manifest: Mapping[str, Any],
        revisions: tuple[str, ...],
        directory: Path,
        resources: Mapping[str, str],
    ) -> None:
        self.revisions = revisions
        self.directory = directory
        try:
            registered = _Registrations.model_validate(manifest)
        except ValidationError as error:
            raise MigrationError("invalid_bundle", "Domain registration has an invalid shape.") from error
        steps = registered.domain_steps
        self.readiness = registered.domain_readiness
        self._by_id = {step.id: step for step in steps}
        self._validate_steps(steps, resources)
        self.steps = self._ordered(steps)
        self._validate_readiness()
        self._modules: dict[str, dict[str, Any]] = {}
        for step in self.steps:
            if step.module not in self._modules:
                module = runpy.run_path(str(self.directory / step.module))
                if not all(callable(module.get(name)) for name in ("preflight", "apply", "verify", "validate")):
                    raise MigrationError(
                        "invalid_bundle", "Domain modules must implement all four maintenance operations."
                    )
                self._modules[step.module] = module

    def _validate_steps(self, steps: tuple[DomainStep, ...], resources: Mapping[str, str]) -> None:
        if len(self._by_id) != len(steps):
            raise MigrationError("invalid_bundle", "Domain step identifiers must be unique.")
        for step in steps:
            if step.revision not in self.revisions:
                raise MigrationError("invalid_bundle", "A domain step requires an unknown schema revision.")
            if not step.module.endswith(".py") or any(name not in resources for name in (step.module, *step.resources)):
                raise MigrationError("invalid_bundle", "Domain resources must be frozen inside the migration bundle.")
            for dependency in step.depends_on:
                required = self._by_id.get(dependency)
                if required is None or self.revisions.index(required.revision) > self.revisions.index(step.revision):
                    raise MigrationError("invalid_bundle", "A domain dependency is missing or requires a later schema.")
                if not set(step.backends) <= set(required.backends):
                    raise MigrationError("invalid_bundle", "A domain dependency is unavailable on a declared backend.")

    def _ordered(self, steps: tuple[DomainStep, ...]) -> tuple[DomainStep, ...]:
        ordered: list[DomainStep] = []
        done: set[str] = set()
        for revision in self.revisions:
            remaining = [step for step in steps if step.revision == revision]
            while remaining:
                available = next((step for step in remaining if set(step.depends_on) <= done), None)
                if available is None:
                    raise MigrationError("invalid_bundle", "Domain dependencies contain a cycle.")
                ordered.append(available)
                done.add(available.id)
                remaining.remove(available)
        return tuple(ordered)

    def _validate_readiness(self) -> None:
        if self.steps:
            first = min(self.revisions.index(step.revision) for step in self.steps)
            if not set(self.revisions[first:]) <= self.readiness.keys():
                raise MigrationError("invalid_bundle", "Every domain-managed revision needs explicit live validators.")
        for revision, ids in self.readiness.items():
            if revision not in self.revisions or len(ids) != len(set(ids)):
                raise MigrationError("invalid_bundle", "A live validation registration is invalid.")
            for step_id in ids:
                step = self._by_id.get(step_id)
                if step is None or self.revisions.index(step.revision) > self.revisions.index(revision):
                    raise MigrationError("invalid_bundle", "A live validator requires a missing or future domain step.")

    def checksum_inputs(self, revision: str, resources: Mapping[str, str]) -> dict[str, Any]:
        steps = [step for step in self.steps if step.revision == revision]
        if not steps and revision not in self.readiness:
            return {}
        ids = self.readiness.get(revision, ())
        used = [*steps, *(self._by_id[step_id] for step_id in ids)]
        return {
            "steps": [step.model_dump(mode="json") for step in steps],
            "readiness": ids,
            "resources": {name: resources[name] for step in used for name in (step.module, *step.resources)},
        }

    def pending(self, revision: str | None, backend: Backend) -> tuple[DomainStep, ...]:
        if revision is not None and revision not in self.revisions:
            raise MigrationError("incompatible_schema", "Database revision is unknown to this bundle.")
        position = -1 if revision is None else self.revisions.index(revision)
        pending = tuple(step for step in self.steps if self.revisions.index(step.revision) > position)
        for step in pending:
            self._require_backend(step, backend)
        return pending

    def preflight(
        self, connection: Connection, revision: str | None, *, backend: Backend, configuration: Mapping[str, Any]
    ) -> None:
        for step in self.pending(revision, backend):
            self._invoke(step, "preflight", connection, configuration)

    def apply(
        self,
        connection: Connection,
        revision: str,
        *,
        backend: Backend,
        configuration: Mapping[str, Any],
        guard: Callable[[], None] = lambda: None,
    ) -> None:
        for step in self.steps:
            if step.revision == revision:
                self._require_backend(step, backend)
                guard()
                self._invoke(step, "preflight", connection, configuration)
                guard()
                self._invoke(step, "apply", connection, configuration)
                guard()
                self._invoke(step, "verify", connection, configuration)
                guard()

    def validate(
        self, connection: Connection, revision: str | None, *, backend: Backend, configuration: Mapping[str, Any]
    ) -> None:
        if revision is None:
            return
        for step_id in self.readiness.get(revision, ()):
            step = self._by_id[step_id]
            self._require_backend(step, backend)
            self._invoke(step, "validate", connection, configuration)

    @staticmethod
    def _require_backend(step: DomainStep, backend: Backend) -> None:
        if backend not in step.backends:
            raise MigrationError(
                "unsupported_upgrade_path", "A required domain step has no registered backend support."
            )

    def _invoke(
        self, step: DomainStep, operation: str, connection: Connection, configuration: Mapping[str, Any]
    ) -> None:
        module = self._modules[step.module]
        try:
            module[operation](connection, configuration=copy.deepcopy(dict(configuration)))
        except MigrationError:
            raise
        except Exception as error:
            raise MigrationError(
                "domain_validation_failed",
                f"Domain {step.id} failed during {operation}; stored data requires maintenance.",
            ) from error
