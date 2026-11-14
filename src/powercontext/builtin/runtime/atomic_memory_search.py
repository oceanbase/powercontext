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

"""Atomic Family adapter for the common Artifact search service."""

from powercontext.artifacts import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory.search import AtomicArtifactSearchRequest
from powercontext.builtin.authz import AccessDeniedError, AccessIdentityRequiredError, PrincipalRef
from powercontext.builtin.runtime.atomic_memory import AtomicMemoryApplication, AtomicMemorySearchPage
from powercontext.builtin.sources import validate_scope_id


class AtomicArtifactSearcher:
    family = "atomic-memory"
    request_type = AtomicArtifactSearchRequest

    def __init__(self, application: AtomicMemoryApplication) -> None:
        self.application = application

    async def search(
        self,
        scope_id: str,
        request: AtomicArtifactSearchRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> AtomicMemorySearchPage:
        if execution_context is not None:
            context = execution_context
            if type(context.trusted_local) is not bool or (context.trusted_local and context.access is not None):
                raise AccessDeniedError()
            if context.principal is None:
                if not context.trusted_local or context.access is not None:
                    raise AccessIdentityRequiredError()
            elif not isinstance(context.principal, PrincipalRef):
                raise AccessIdentityRequiredError()
            if context.access is None and not context.trusted_local:
                raise AccessDeniedError()
        return await self.application.for_scope(validate_scope_id(scope_id)).search(
            request.query, context=execution_context, artifact_request=request
        )
