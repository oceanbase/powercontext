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

"""A Gateway can only receive a Core-approved target with a privileged identity."""

import httpx
import pytest

from powercontext.server.authz.composition import open_builtin_access_control
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_context_protection_transports import _ADMIN, _OTHER, _app, _create_scope, _settings


@pytest.mark.anyio
async def test_target_is_only_returned_to_privileged_service_on_allowed_before_send(tmp_path):
    settings = _settings(tmp_path)
    prepare_server_database(settings)
    async with open_builtin_access_control(
        settings.database,
        bootstrap_administrators=(_ADMIN, _OTHER),
        deployment_id="context-transports",
    ):
        pass
    app = _app(tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Authorization": "Bearer transport-admin"},
        ) as client,
    ):
        scope = await _create_scope(client, "gateway-target")
        gateway_grant = None
        for principal in (_ADMIN, _OTHER):
            granted = await client.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": principal.type, "id": principal.id},
                    "resource": {"type": "scope", "scope_id": scope},
                    "role": "scope.viewer",
                    "idempotency_key": f"gateway-target-{principal.id}",
                },
            )
            assert granted.status_code == 201, granted.text
        request = {
            "scope_id": scope,
            "phase": "before_send",
            "target_profile": "model",
            "payload": {
                "schema": "powercontext.model-json-payload.v1",
                "headers": {"content-type": "application/json"},
                "body": '{"model":"test","max_tokens":64,"messages":[{"role":"user","content":"hello"}]}',
            },
            "prepared_contexts": [],
        }
        ordinary = await client.post("/v1/context/check", json=request)
        assert ordinary.status_code == 200, ordinary.text
        assert ordinary.json()["decision"] == "allow"
        assert "target_endpoint" not in ordinary.json()

        # An administrator's deployment powers do not implicitly grant paid gateway invocation.
        ungranted = await client.post(
            "/v1/context/check", json=request, headers={"X-PowerContext-Resolve-Target": "true"}
        )
        assert ungranted.status_code == 403, ungranted.text
        for principal in (_ADMIN, _OTHER):
            granted = await client.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": principal.type, "id": principal.id},
                    "resource": {"type": "scope", "scope_id": scope},
                    "role": "gateway.user",
                    "idempotency_key": f"gateway-invoke-{principal.id}",
                },
            )
            assert granted.status_code == 201, granted.text
            if principal == _ADMIN:
                gateway_grant = granted.json()
        privileged = await client.post(
            "/v1/context/check", json=request, headers={"X-PowerContext-Resolve-Target": "true"}
        )
        assert privileged.status_code == 200, privileged.text
        assert privileged.json()["decision"] == "allow"
        assert privileged.json()["target_endpoint"] == "https://model.example/v1/chat/completions"
        assert privileged.json()["target_protocol"] == "openai-chat"
        assert privileged.json()["binding"]

        other_scope = await _create_scope(client, "gateway-other-scope")
        granted = await client.post(
            "/v1/access/bindings/create",
            json={
                "subject": {"type": _ADMIN.type, "id": _ADMIN.id},
                "resource": {"type": "scope", "scope_id": other_scope},
                "role": "scope.viewer",
                "idempotency_key": "gateway-other-viewer",
            },
        )
        assert granted.status_code == 201, granted.text
        other_target = await client.post(
            "/v1/context/check",
            json={**request, "scope_id": other_scope},
            headers={"X-PowerContext-Resolve-Target": "true"},
        )
        assert other_target.status_code == 403, other_target.text

        user = await client.post(
            "/v1/context/check",
            json=request,
            headers={"Authorization": "Bearer transport-other", "X-PowerContext-Resolve-Target": "true"},
        )
        assert user.status_code == 403
        blocked = await client.post(
            "/v1/context/check",
            json={
                **request,
                "payload": {
                    **request["payload"],
                    "body": request["payload"]["body"].replace("hello", "synthetic-transport-secret"),
                },
            },
            headers={"X-PowerContext-Resolve-Target": "true"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["decision"] == "block"
        assert "target_endpoint" not in blocked.json()

        assert gateway_grant is not None
        revoked = await client.post(
            "/v1/access/bindings/revoke",
            json={
                "binding_id": gateway_grant["binding_id"],
                "expected_version": gateway_grant["version"],
                "idempotency_key": "revoke-gateway-target-invocation",
            },
        )
        assert revoked.status_code == 200, revoked.text
        denied_after_revoke = await client.post(
            "/v1/context/check", json=request, headers={"X-PowerContext-Resolve-Target": "true"}
        )
        assert denied_after_revoke.status_code == 403
        # Revocation denies the next admission; it does not retroactively invalidate the earlier allow.
        ordinary_after_revoke = await client.post("/v1/context/check", json=request)
        assert ordinary_after_revoke.status_code == 200
        assert ordinary_after_revoke.json()["decision"] == "allow"
        assert "target_endpoint" not in ordinary_after_revoke.json()
