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

"""Scope-bound Dream operations exposed by Builtin Runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

from powercontext.builtin.dream.bindings import DREAM_BINDINGS
from powercontext.builtin.dream.models import (
    CreateDreamRunRequest,
    DreamError,
    DreamRun,
    DreamRunPage,
    GetDreamRunRequest,
    ListDreamRunsRequest,
)
from powercontext.builtin.sources import validate_scope_id

if TYPE_CHECKING:
    from powercontext.builtin.dream.service import DreamService
    from powercontext.builtin.runtime.application import BuiltinRuntime


class ScopedDreamApplication:
    def __init__(self, runtime: BuiltinRuntime, scope_id: str, principal_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)
        self.principal_id = principal_id

    def _service(self) -> DreamService:
        if self._runtime._dream_service is None:
            raise DreamError("capability_unavailable")
        return self._runtime._dream_service

    async def create(self, request: CreateDreamRunRequest, /) -> DreamRun:
        async with self._runtime._scoped_operation(self.scope_id):
            run = await self._service().create(self.scope_id, self.principal_id, request)
        if not run.terminal and self._runtime.artifact_processing_supervisor is not None:
            self._runtime.artifact_processing_supervisor.wake(DREAM_BINDINGS[run.operation])
        return run

    async def get(self, request: GetDreamRunRequest, /) -> DreamRun:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().get(self.scope_id, self.principal_id, request.run_id)

    async def list(self, request: ListDreamRunsRequest, /) -> DreamRunPage:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._service().list(self.scope_id, self.principal_id, request)


class DreamApplication:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /, *, principal_id: str = "runtime") -> ScopedDreamApplication:
        return ScopedDreamApplication(self._runtime, scope_id, principal_id)
