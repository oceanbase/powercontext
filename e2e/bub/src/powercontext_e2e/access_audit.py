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

"""Find what an ON run reached outside its own Scope, from the Server's Access audit.

Every ON integration holds the harness Client's token, which can read every Scope on the Server, including the
Scopes of earlier trials that hold the same workload's answer. An enforced Server records each authorization decision
it makes for an API or MCP request, allowed or denied, so the audit shows whether anything used that token on another
Scope while an arm ran.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from powercontext.client import ServerResponseError
from powercontext.http import ListAccessAuditRequest

from .models import OutsideScopeAccess

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from powercontext.client import PowerContextClient
    from powercontext.http import AccessAuditEvent

# Server background processing records its own decisions under the transport "background"; it is not the agent.
REQUEST_TRANSPORTS = frozenset({"http", "mcp"})
# The Server authorizes these once for the whole Server rather than per Scope, yet they return Scope content: a
# Handoff Report over every Scope or a subtree carries each Scope's latest Handoff. Its other Server-wide operations
# return descriptors, settings, or counts, such as listing Scopes.
SERVER_WIDE_CONTENT_OPERATIONS = frozenset({"get_handoff_report"})
# Requests that can record decisions on another Scope yet return none of its Memory, Artifacts, or Handoffs: a
# Scope's descriptor, its counts, its resolved identity, or processing cursors. A host plugin's own end-of-session
# flush of an earlier ON arm's Scope can also still be extracting Memory, and recording decisions on that Scope, after
# its arm ended.
CONTENTLESS_OPERATIONS = frozenset({"get_scope", "get_stats", "resolve_scope_selection", "flush_memory"})
# The Access API returns permission decisions, roles, Bindings, and audit records. Checking whether the caller may read
# a Scope records that decision on the Scope, and listing its Bindings or audit records an administration check there.
ACCESS_OPERATIONS = frozenset({
    "get_access_principal",
    "check_access",
    "list_access_resources",
    "list_access_roles",
    "list_access_bindings",
    "create_access_binding",
    "revoke_access_binding",
    "replace_access_binding",
    "list_access_audit",
})
# Checks the Server reports to the caller rather than enforces: which actions a Candidate offers, and the Access API's
# permission check. Denying one does not reject the request that asked.
PROBE_OPERATIONS = frozenset({"candidate_permissions", "check_access"})
_PAGE_LIMIT = 500


class AccessAuditUnavailableError(RuntimeError):
    """Report a Server whose Access audit the harness cannot read, so it could not tell a contaminated ON run."""

    def __init__(self, deployment_id: str, error: ServerResponseError) -> None:
        super().__init__(
            f"The harness cannot read the Server's Access audit for deployment {deployment_id!r} "
            f"({error.status_code} {error.code}). It reads the audit to find ON runs that reached another Scope; set "
            "POWERCONTEXT_E2E_ACCESS_DEPLOYMENT_ID to the Server's POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID"
        )


async def require_access_audit(client: PowerContextClient, deployment_id: str) -> None:
    """Fail before the first run when the harness Client cannot list the deployment's audit."""

    try:
        await client.list_access_audit(_request(deployment_id, None, None, limit=1))
    except ServerResponseError as error:
        raise AccessAuditUnavailableError(deployment_id, error) from error


async def outside_scope_accesses(
    client: PowerContextClient,
    *,
    deployment_id: str,
    scope_id: str,
    start: datetime,
    end: datetime,
) -> tuple[OutsideScopeAccess, ...]:
    """Return the API and MCP decisions recorded from ``start`` until ``end`` about anything but ``scope_id``.

    The Server-wide audit holds every Scope's events, so one listing covers the window however many Scopes exist.
    The window is compared with the Server's clock, and the harness itself touches only the run's own Scope inside it.
    """

    events: list[AccessAuditEvent] = []
    cursor: str | None = None
    while True:
        page = await client.list_access_audit(_request(deployment_id, (start, end), cursor, limit=_PAGE_LIMIT))
        events.extend(page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    return outside_scope(events, scope_id)


def outside_scope(events: Sequence[AccessAuditEvent], scope_id: str) -> tuple[OutsideScopeAccess, ...]:
    """Keep the API and MCP decisions about another Scope, or about the Server as a whole, such as listing Scopes.

    A request can be allowed one check and denied the next, which rejects it, so each decision also records whether
    its request was denied any check the Server enforces, on any Scope. A request's later checks can be recorded under
    another operation, such as the write an approval needs, or under the background transport, such as a Dream review's
    read of its evidence, so only the request ID ties them together. Probes do not reject the request.
    """

    rejected = {
        event.request_id
        for event in events
        if event.request_id is not None and not event.allowed and event.operation not in PROBE_OPERATIONS
    }
    accesses: list[OutsideScopeAccess] = []
    for event in events:
        resource = event.resource.root
        target = getattr(resource, "scope_id", None)
        if event.transport not in REQUEST_TRANSPORTS or target == scope_id:
            continue
        accesses.append(
            OutsideScopeAccess(
                occurred_at=event.occurred_at,
                request_id=event.request_id,
                transport=event.transport,
                operation=event.operation,
                action=event.action.value,
                scope_id=target,
                allowed=event.allowed,
                request_rejected=not event.allowed if event.request_id is None else event.request_id in rejected,
            )
        )
    return tuple(accesses)


def reached_another_scope(accesses: Sequence[OutsideScopeAccess]) -> bool:
    """Whether the Server allowed the run a request on another Scope, or a Server-wide request returning Scope content.

    Any request on another Scope counts except those that return no content, including the Access API's, and those the
    Server rejected: a write there is no use to the run, but it shows the integration acting outside its Scope, as when
    it falls back to the Default Scope.
    """

    return any(
        access.allowed
        and not access.request_rejected
        and (
            access.operation in SERVER_WIDE_CONTENT_OPERATIONS
            if access.scope_id is None
            else access.operation not in CONTENTLESS_OPERATIONS | ACCESS_OPERATIONS
        )
        for access in accesses
    )


def _request(
    deployment_id: str,
    window: tuple[datetime, datetime] | None,
    cursor: str | None,
    *,
    limit: int,
) -> ListAccessAuditRequest:
    return ListAccessAuditRequest.model_validate({
        "resource": {"type": "server", "deployment_id": deployment_id},
        "time_range": None if window is None else {"start": window[0], "end": window[1]},
        "cursor": cursor,
        "limit": limit,
    })
