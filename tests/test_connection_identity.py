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

"""Connection identity is request evidence, independently of readiness and observation permission."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.authentication import AuthenticationUnavailableError, ProviderReadiness
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


@pytest.mark.parametrize(("authenticated", "mode"), [(False, "disabled"), (True, "enforced")])
def test_connection_identity_observes_actual_modes_without_readiness(authenticated, mode, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(),
            auth=BearerAuthConfig(enabled=authenticated, token=SecretStr("fixture-token") if authenticated else None),
            access=AccessControlConfig(mode=mode),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:

        async def forbidden_probe():
            pytest.fail("Connection identity must not invoke readiness")

        app.state.readiness_probe = forbidden_probe
        headers = {"Authorization": "Bearer fixture-token"} if authenticated else {}
        result = client.get("/v1/connection/identity", headers=headers)
        assert result.status_code == 200, result.text
        identity = result.json()
        assert identity["server_id"] == client.get("/v1/server-info", headers=headers).json()["server_id"]
        assert identity["access_mode"] == mode
        assert identity["authentication"] == ("authenticated" if authenticated else "disabled")
        assert (identity["principal"] is not None) == authenticated
        if authenticated:
            assert client.get("/v1/connection/identity").status_code == 401
            assert client.get("/v1/connection/identity", headers={"Authorization": "Bearer wrong"}).status_code == 401
        else:
            # An unavailable enforced service must never be reported as anonymous.
            app.state.access_mode = "enforced"
            assert client.get("/v1/connection/identity").status_code == 503


def test_unavailable_authentication_is_not_anonymous_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class UnavailableAuthentication:
        armed = False

        async def authenticate(self, request):
            raise AuthenticationUnavailableError

        async def readiness(self):
            if self.armed:
                pytest.fail("Identity must authenticate requests without readiness probes")
            return ProviderReadiness(ready=True)

    provider = UnavailableAuthentication()
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(),
            access=AccessControlConfig(mode="enforced"),
            mcp=McpConfig(enabled=False),
        ),
        authentication_provider=provider,
    )
    with TestClient(app) as client:
        provider.armed = True
        response = client.get("/v1/connection/identity")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "authentication_unavailable"
