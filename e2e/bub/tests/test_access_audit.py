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

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from powercontext.client import ForbiddenResponseError
from powercontext.http import AccessAuditEvent

from powercontext_e2e.access_audit import (
    ACCESS_OPERATIONS,
    SERVER_WIDE_CONTENT_OPERATIONS,
    AccessAuditUnavailableError,
    outside_scope,
    outside_scope_accesses,
    reached_another_scope,
    require_access_audit,
)

_REPOSITORY = Path(__file__).resolve().parents[3]
_OWN = "scp_own"
_EARLIER = "scp_earlier_trial"
_START = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _event(
    resource: dict[str, object],
    *,
    transport: str = "http",
    operation: str = "search_memory",
    action: str = "scope.read",
    allowed: bool = True,
    cursor: int = 1,
    request_id: str | None = None,
) -> AccessAuditEvent:
    return AccessAuditEvent.model_validate({
        "cursor": cursor,
        "event_id": f"evt-{cursor}",
        "occurred_at": _START + timedelta(seconds=cursor),
        "request_id": request_id,
        "transport": transport,
        "operation": operation,
        "principal": {"type": "service", "id": "server-token"},
        "actor": None,
        "action": action,
        "resource": resource,
        "allowed": allowed,
        "reason_code": "allowed" if allowed else "denied",
        "policy_revision": None,
        "matched_subject": None,
        "binding_id": None,
        "target": None,
        "role": None,
        "expected_version": None,
        "result_version": None,
    })


def _scope(scope_id: str) -> dict[str, object]:
    return {"type": "scope", "scope_id": scope_id}


def _artifact(scope_id: str) -> dict[str, object]:
    return {
        "type": "artifact",
        "scope_id": scope_id,
        "identity": {"family": "atomic-memory", "artifact_id": "art_1"},
        "selector": None,
    }


_SERVER = {"type": "server", "deployment_id": "powercontext"}


def test_outside_scope_keeps_api_and_mcp_decisions_about_other_scopes_and_the_server() -> None:
    accesses = outside_scope(
        (
            _event(_scope(_OWN), operation="prepare_context"),
            _event(_artifact(_OWN), action="artifact.read", operation="get_artifact"),
            _event(_scope(_EARLIER), operation="list_memory_entries", cursor=2),
            _event(_artifact(_EARLIER), transport="mcp", action="artifact.read", operation="get_artifact", cursor=3),
            _event(_SERVER, transport="mcp", action="server.observe", operation="list_scopes", cursor=4),
            # Server background processing is not the agent.
            _event(_scope(_EARLIER), transport="background", action="scope.contribute", operation="processing"),
        ),
        _OWN,
    )

    assert [(access.transport, access.operation, access.scope_id) for access in accesses] == [
        ("http", "list_memory_entries", _EARLIER),
        ("mcp", "get_artifact", _EARLIER),
        ("mcp", "list_scopes", None),
    ]
    assert reached_another_scope(accesses)


@pytest.mark.parametrize(
    "events",
    [
        pytest.param((_event(_SERVER, action="server.observe", operation="list_scopes"),), id="listing-scopes"),
        pytest.param((_event(_scope(_EARLIER), allowed=False),), id="denied"),
        # An earlier ON arm's plugin flush can still be extracting into that arm's Scope; a flush returns no content.
        pytest.param(
            (
                _event(_scope(_EARLIER), action="scope.contribute", operation="flush_memory"),
                _event(_artifact(_EARLIER), action="artifact.write", operation="flush_memory"),
            ),
            id="earlier-flush",
        ),
        # A Scope's descriptor or counts hold none of its Memory, as listing Scopes holds none.
        pytest.param(
            (
                _event(_scope(_EARLIER), operation="get_scope"),
                _event(_scope(_EARLIER), operation="get_stats"),
            ),
            id="descriptor-and-counts",
        ),
        # Asking whether the caller may read another Scope returns a decision, not the Scope's content.
        pytest.param(
            (
                _event(_scope(_EARLIER), operation="check_access"),
                _event(_artifact(_EARLIER), action="artifact.read", operation="check_access"),
            ),
            id="permission-check",
        ),
        pytest.param((), id="nothing"),
    ],
)
def test_a_run_reaches_another_scope_only_through_a_request_that_can_return_its_content(events) -> None:
    assert not reached_another_scope(outside_scope(events, _OWN))


def test_a_request_the_server_rejected_does_not_reach_another_scope() -> None:
    # Allowed to contribute to the Scope but denied the write, the request fails without touching its content.
    events = (
        _event(_scope(_EARLIER), action="scope.contribute", operation="propose_experience", request_id="req-1"),
        _event(
            _artifact(_EARLIER),
            action="artifact.write",
            operation="propose_experience",
            allowed=False,
            request_id="req-1",
            cursor=2,
        ),
    )

    accesses = outside_scope(events, _OWN)

    assert [(access.request_id, access.request_rejected) for access in accesses] == [("req-1", True), ("req-1", True)]
    assert not reached_another_scope(accesses)


def test_a_denial_on_the_runs_own_scope_rejects_the_whole_request() -> None:
    events = (
        _event(_scope(_EARLIER), operation="prepare_context", request_id="req-1"),
        _event(_scope(_OWN), operation="prepare_context", allowed=False, request_id="req-1", cursor=2),
    )

    assert not reached_another_scope(outside_scope(events, _OWN))


@pytest.mark.parametrize(
    "denial",
    [
        # Approving a tag change on an Experience another principal owns needs a write the token is denied.
        pytest.param(
            _event(_artifact(_EARLIER), action="artifact.write", operation="review_catalog_change", request_id="req-1"),
            id="catalog-change-write",
        ),
        pytest.param(
            _event(_scope(_EARLIER), action="scope.admin", operation="review_candidate_target", request_id="req-1"),
            id="candidate-target-write",
        ),
        # A Dream review records the checks on its target under the background transport, inside the request.
        pytest.param(
            _event(
                _artifact(_EARLIER),
                transport="background",
                action="artifact.write",
                operation="artifact_dreaming",
                request_id="req-1",
            ),
            id="dream-review-write",
        ),
    ],
)
def test_a_denial_under_another_operation_of_the_same_request_rejects_it(denial) -> None:
    allowed = _event(_scope(_EARLIER), action="scope.review", operation="approve_candidate", request_id="req-1")
    events = (allowed, denial.model_copy(update={"allowed": False, "cursor": 2}))

    accesses = outside_scope(events, _OWN)

    assert accesses[0].request_rejected
    assert not reached_another_scope(accesses)


@pytest.mark.parametrize(
    "denial",
    [
        pytest.param(
            _event(_scope(_EARLIER), action="scope.review", operation="candidate_permissions", request_id="req-1"),
            id="candidate-permissions-probe",
        ),
        pytest.param(
            _event(
                _artifact(_EARLIER), action="artifact.write", operation="get_artifact_candidate", request_id="req-2"
            ),
            id="another-request",
        ),
    ],
)
def test_a_probe_or_another_requests_denial_does_not_reject_a_request(denial) -> None:
    allowed = _event(_scope(_EARLIER), operation="get_artifact_candidate", request_id="req-1", cursor=2)
    denied = denial.model_copy(update={"allowed": False})

    assert reached_another_scope(outside_scope((denied, allowed), _OWN))


def test_writing_to_another_scope_reaches_it() -> None:
    # An integration that falls back to the Default Scope, for example, acts outside the arm's Scope.
    events = (_event(_scope(_EARLIER), action="scope.contribute", operation="capture_content_source"),)

    assert reached_another_scope(outside_scope(events, _OWN))


def test_a_handoff_report_over_every_scope_reaches_them_although_it_is_authorized_server_wide() -> None:
    # Selection modes other than exact are authorized once on the Server, but the report carries each Scope's Handoff.
    events = (_event(_SERVER, transport="mcp", action="server.observe", operation="get_handoff_report"),)

    assert reached_another_scope(outside_scope(events, _OWN))


# Server-wide operations reviewed as returning no Scope content; a new one must be classified before it is trusted.
_SERVER_WIDE_WITHOUT_CONTENT = frozenset({
    "get_server_info",
    "get_capabilities",
    "list_scopes",
    "create_scope",
    "get_default_scope",
    "set_default_scope",
    "resolve_scope_selection",
    "resolve_scope_binding",
    "set_scope_binding",
    "clear_scope_binding",
    "register_source_definition",
    "list_artifact_plugins",
    "get_artifact_plugin",
    "enable_all_artifact_plugins",
    "reload_artifact_plugin",
    "enable_artifact_plugin",
    "disable_artifact_plugin",
    "unload_artifact_plugin",
    "load_source_write_plugin",
    "source_configure",
    "pc_source_instance_set_enabled",
    "scan_external_skills",
    "list_external_skills",
    "resolve_external_skill",
    "get_stats",
})


def test_every_server_wide_operation_is_classified_by_whether_it_returns_scope_content() -> None:
    contract = yaml.safe_load((_REPOSITORY / "openapi" / "powercontext.yaml").read_text(encoding="utf-8"))
    server_wide, access_api = set(), set()
    for operations in contract["paths"].values():
        for operation in operations.values():
            access = operation.get("x-powercontext-access") if isinstance(operation, dict) else None
            if access is None:
                continue
            if access.get("action") == "access.self":
                access_api.add(operation["operationId"])
            elif access.get("resolver") == "scope_selection_read_access" or access.get("resource", {}).get("type") == (
                "server"
            ):
                server_wide.add(operation["operationId"])

    assert server_wide == SERVER_WIDE_CONTENT_OPERATIONS | _SERVER_WIDE_WITHOUT_CONTENT
    # The Access API authorizes the caller itself and then decides about any resource it names, returning no content.
    assert access_api == ACCESS_OPERATIONS


class _AuditClient:
    def __init__(self, pages: list[tuple[list[AccessAuditEvent], str | None]]) -> None:
        self._pages = iter(pages)
        self.requests = []

    async def list_access_audit(self, request):
        self.requests.append(request)
        items, next_cursor = next(self._pages)
        return SimpleNamespace(items=items, next_cursor=next_cursor)


def test_outside_scope_accesses_read_every_page_of_the_server_wide_audit_for_the_window() -> None:
    client = _AuditClient([
        ([_event(_scope(_OWN))], "page-2"),
        ([_event(_scope(_EARLIER), cursor=2)], None),
    ])
    end = _START + timedelta(minutes=5)

    accesses = asyncio.run(outside_scope_accesses(client, deployment_id="lab", scope_id=_OWN, start=_START, end=end))

    assert [access.scope_id for access in accesses] == [_EARLIER]
    first, second = client.requests
    assert first.resource.model_dump() == {"type": "server", "deployment_id": "lab"}
    assert (first.time_range.start, first.time_range.end, first.cursor) == (_START, end, None)
    assert second.cursor == "page-2"


def test_paired_runs_need_an_access_audit_the_harness_can_read() -> None:
    class Refusing:
        async def list_access_audit(self, request):
            raise ForbiddenResponseError(status_code=403, request_id=None, code="access_denied")

    with pytest.raises(AccessAuditUnavailableError, match="POWERCONTEXT_E2E_ACCESS_DEPLOYMENT_ID"):
        asyncio.run(require_access_audit(Refusing(), "powercontext"))
