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

"""Server-owned opt-in persistence for Runtime prepare diagnostics."""

from powercontext.builtin.persistence.context_prepare_diagnostics import ContextPrepareDiagnosticsRepository
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.server.context import current_principal, current_request_id


def diagnostic_sink(application):
    if not isinstance(application, BuiltinRuntime):
        return None
    principal = current_principal()
    request_id = current_request_id()
    from opentelemetry import trace

    span = trace.get_current_span().get_span_context()
    trace_id = format(span.trace_id, "032x") if span.is_valid else None

    async def persist(record):
        record.update(
            principal_type=None if principal is None else principal.type,
            principal_id=None if principal is None else principal.id,
            request_id=request_id,
            trace_id=trace_id,
        )
        async with application.primary_database.transaction() as connection:
            await ContextPrepareDiagnosticsRepository().append(connection, record)

    return persist
