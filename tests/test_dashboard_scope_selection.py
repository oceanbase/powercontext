# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Default selection never substitutes for authority over the selected Scope."""

import sqlite3
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from powercontext.builtin.authz import AccessAction, PrincipalRef
from powercontext.builtin.authz.casbin import CasbinAuthorizationProvider
from powercontext.builtin.persistence.tables import SCOPE_SETTINGS_TABLE
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


def _select_default(client: TestClient, title: str):
    scope = create_scope(client, title)
    result = client.put("/v1/scopes/default", json={"scope_id": scope["scope_id"]})
    assert result.status_code == 200, result.text
    return scope


def _revoke(client: TestClient, binding):
    result = client.post(
        "/v1/access/bindings/revoke",
        json={
            "binding_id": binding["binding_id"],
            "expected_version": binding["version"],
            "idempotency_key": f"revoke-{binding['binding_id']}",
        },
    )
    assert result.status_code == 200, result.text


def _bindings(client: TestClient, resource):
    result = client.post("/v1/access/bindings/list", json={"management_resource": resource})
    assert result.status_code == 200, result.text
    return result.json()["items"]


def _revoke_observer(client: TestClient):
    access = cast(FastAPI, client.app).state.access_control
    bindings = _bindings(client, {"type": "server", "deployment_id": access.deployment_id})
    observer = next(binding for binding in bindings if binding["role"] == "server.observer")
    _revoke(client, observer)


def _grant(client: TestClient, subject, resource, role):
    result = client.post(
        "/v1/access/bindings/create",
        json={"subject": subject, "resource": resource, "role": role, "idempotency_key": f"reader-{role}"},
    )
    assert result.status_code == 201, result.text
    return result.json()


def test_default_selection_requires_observer_even_with_scope_access(dashboard: TestClient) -> None:
    scope = _select_default(dashboard, "Observed default scope")
    assert dashboard.get("/dashboard/home").status_code == 200
    _revoke_observer(dashboard)
    for page in ("home", "notes", "usage", "handoff", "methods", "topics", "prompts", "profile", "review"):
        denied = dashboard.get(f"/dashboard/{page}")
        assert denied.status_code == 403, denied.text
        assert scope["title"] not in denied.text
        assert scope["summary"] not in denied.text
        explicit = dashboard.get(f"/dashboard/{page}", params={"scope": scope["scope_id"]})
        assert explicit.status_code == 200, explicit.text
        assert scope["title"] in explicit.text


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
def test_default_exact_share_is_independent_of_observer_and_scope_grants(
    dashboard: TestClient, monkeypatch, provider: str
) -> None:
    access = cast(FastAPI, dashboard.app).state.access_control
    if provider == "casbin":
        monkeypatch.setattr(
            access,
            "provider",
            CasbinAuthorizationProvider(access.relationships, deployment_id=access.deployment_id),
        )
    scope = _select_default(dashboard, "Hidden default scope title")
    created = dashboard.post(
        f"/v1/scopes/{scope['scope_id']}/artifacts",
        json={"family": "atomic-memory", "content": {"kind": "fact", "text": "Only this exact note is shared."}},
    )
    assert created.status_code == 201, created.text
    artifact = created.json()["artifact_id"]
    neighbor = dashboard.post(
        f"/v1/scopes/{scope['scope_id']}/artifacts",
        json={"family": "atomic-memory", "content": {"kind": "fact", "text": "Private neighboring note."}},
    )
    assert neighbor.status_code == 201, neighbor.text
    reader = {"type": "user", "id": "default-scope-reader"}
    _grant(dashboard, reader, {"type": "server", "deployment_id": access.deployment_id}, "server.observer")
    scope_grant = _grant(dashboard, reader, {"type": "scope", "scope_id": scope["scope_id"]}, "scope.viewer")
    exact_grant = _grant(
        dashboard,
        reader,
        {
            "type": "artifact",
            "scope_id": scope["scope_id"],
            "identity": {"family": "atomic-memory", "artifact_id": artifact},
        },
        "artifact.viewer",
    )
    _revoke(dashboard, scope_grant)
    authentication = cast(FastAPI, dashboard.app).state.authentication_provider
    query = {"artifact": artifact, "revision": 1, "history": "1"}
    with monkeypatch.context() as patch:
        # Select a different authenticated subject; all grants and content use HTTP.
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        shared = dashboard.get("/dashboard/notes", params=query)
        assert shared.status_code == 200, shared.text
        assert "Only this exact note is shared." in shared.text
        assert "Private neighboring note." not in shared.text
        assert scope["title"] not in shared.text
        assert scope["summary"] not in shared.text
        for page in ("home", "notes", "usage", "handoff", "methods", "topics", "prompts", "profile", "review"):
            assert dashboard.get(f"/dashboard/{page}").status_code == 403
    _revoke(dashboard, exact_grant)
    with monkeypatch.context() as patch:
        patch.setattr(authentication, "_principal", PrincipalRef(**reader))
        denied = dashboard.get("/dashboard/notes", params=query)
    assert denied.status_code == 403, denied.text
    assert "Only this exact note is shared." not in denied.text
    assert scope["title"] not in denied.text


@pytest.mark.parametrize("path", ["/dashboard/methods", "/dashboard/notes?q=evidence&mode=text"])
def test_default_page_requires_scope_access(dashboard: TestClient, path: str) -> None:
    scope = _select_default(dashboard, "Fallback private scope")
    for binding in _bindings(dashboard, {"type": "scope", "scope_id": scope["scope_id"]}):
        if binding["resource"]["type"] == "scope":
            _revoke(dashboard, binding)
    response = dashboard.get(path)
    assert response.status_code == 403, response.text
    assert scope["title"] not in response.text
    assert scope["summary"] not in response.text


def test_dangling_default_binding_is_reported_without_repair(dashboard: TestClient) -> None:
    scope = _select_default(dashboard, "Missing default target")
    runtime = cast(FastAPI, dashboard.app).state.application
    # Simulate persisted corruption while keeping the original binding and grants.
    with sqlite3.connect(runtime.primary_database.engine.url.database) as connection:
        connection.execute("DELETE FROM pc_scopes WHERE scope_id = ?", (scope["scope_id"],))
    for page in ("home", "notes", "usage", "handoff", "methods", "topics", "prompts", "profile", "review"):
        response = dashboard.get(f"/dashboard/{page}")
        assert response.status_code == 409, response.text
        assert scope["title"] not in response.text
    unresolved = dashboard.get("/v1/scopes/default")
    assert unresolved.status_code == 409, unresolved.text
    assert unresolved.json()["error"]["code"] == "scope_binding_target_missing"
    assert unresolved.json()["error"]["details"]["scope_id"] == scope["scope_id"]


def test_absent_default_binding_still_requires_observer(dashboard: TestClient) -> None:
    runtime = cast(FastAPI, dashboard.app).state.application

    async def remove_binding():
        async with runtime.primary_database.transaction() as connection:
            await connection.execute(delete(SCOPE_SETTINGS_TABLE).where(SCOPE_SETTINGS_TABLE.c.name == "default"))

    assert dashboard.portal is not None
    dashboard.portal.call(remove_binding)
    assert dashboard.get("/dashboard/home").status_code == 200
    _revoke_observer(dashboard)
    for page in ("home", "notes", "usage", "handoff", "methods", "topics", "prompts", "profile", "review"):
        response = dashboard.get(f"/dashboard/{page}")
        assert response.status_code == 403, response.text


def test_scope_audit_failure_does_not_expose_default_metadata(dashboard: TestClient, monkeypatch) -> None:
    scope = _select_default(dashboard, "Audit protected default metadata")
    audit = cast(FastAPI, dashboard.app).state.access_control.audit
    append = audit.append_audit

    async def fail_scope_decision(event):
        if event.action is AccessAction.SCOPE_READ:
            raise RuntimeError
        return await append(event)

    append_many = audit.append_audits

    async def fail_scope_decisions(events):
        if any(event.action is AccessAction.SCOPE_READ for event in events):
            raise RuntimeError
        return await append_many(events)

    with monkeypatch.context() as patch:
        patch.setattr(audit, "append_audit", fail_scope_decision)
        patch.setattr(audit, "append_audits", fail_scope_decisions)
        response = dashboard.get("/dashboard/methods")
    assert response.status_code == 503, response.text
    assert scope["title"] not in response.text
    assert scope["summary"] not in response.text
    recovered = dashboard.get("/dashboard/methods")
    assert recovered.status_code == 200, recovered.text
    assert scope["title"] in recovered.text
