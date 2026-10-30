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

"""Check real HTTP authorization with each installed release's native SDK.

Run with -I after stopping the CLI Server. Only authentication is supplied;
the installed Server loads and enforces the persisted builtin access policy.
"""

import asyncio
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx

from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings

# Releases up to 1.4.0 keep access control in the Server package; later releases moved it into the built-in
# runtime. This script runs under each installed release's own interpreter, so choose by what is installed.
# find_spec rather than try/except: a broken built-in package must fail loudly, not fall back to the old path.
_AUTHZ = (
    "powercontext.builtin.authz"
    if importlib.util.find_spec("powercontext.builtin.authz")
    else "powercontext.server.authz"
)
PrincipalRef = importlib.import_module(_AUTHZ).PrincipalRef


async def verify(historical):
    token = "isolated-viewer-token"  # noqa: S105 -- disposable fixture credential
    viewer = PrincipalRef(type="user", id="upgrade-viewer")
    settings = ServerSettings()
    # Keep the persisted processing capabilities unchanged. Embedded releases
    # require the all role; use a separate, unprivileged background identity
    # and disable scheduling instead of running workers as the viewer.
    settings = settings.model_copy(
        update={
            "access": settings.access.model_copy(update={"background_principal_id": "upgrade-authz-probe"}),
            "runtime": settings.runtime.model_copy(
                update={
                    "schedule_seconds": None,
                    "experience_schedule_seconds": None,
                }
            ),
        }
    )
    app = create_server_app(
        settings=settings,
        authentication_provider=StaticBearerAuthenticationProvider(token, viewer),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        identity = await client.get("/v1/access/me")
        assert identity.status_code == 200, identity.text
        assert identity.json()["principal"]["type"] == "user"
        assert identity.json()["principal"]["id"] == viewer.id
        scope = historical["scope"]
        allowed = await client.get(f"/v1/scopes/{scope}")
        assert allowed.status_code == 200, allowed.text
        records = await client.post("/v1/memory/entries/list", json={"scope_id": scope})
        assert records.status_code == 200, records.text
        assert {item["text"] for item in records.json()["entries"]} >= {item["text"] for item in historical["memory"]}
        denied_read = await client.get(f"/v1/scopes/{historical['ungranted_scope']}")
        assert denied_read.status_code == 403, denied_read.text
        denied_write = await client.post(
            "/v1/memory/remember",
            json={"scope_id": scope, "kind": "fact", "text": "Viewer must not persist this write."},
        )
        assert denied_write.status_code == 403, denied_write.text
        after = await client.post("/v1/memory/entries/list", json={"scope_id": scope})
        assert after.status_code == 200, after.text
        assert after.json() == records.json(), "Denied writes must not change stored records"
    print("Viewer can read its granted scope; cross-scope reads and writes are denied.")


if __name__ == "__main__":
    asyncio.run(verify(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
