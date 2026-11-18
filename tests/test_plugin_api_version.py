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

"""One plugin version serves a whole plugin API request, also when a reload arrives with its body."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from starlette.datastructures import QueryParams

from powercontext.server import app as server_app


def _version(body: str, host: str):
    route = SimpleNamespace(
        method="POST", path="/upload", body=body, max_body_bytes=None, timeout_seconds=30, handler="upload"
    )
    return SimpleNamespace(spec=SimpleNamespace(api=SimpleNamespace(routes=(route,)))), host


def test_a_reload_while_the_body_arrives_moves_the_whole_request_to_the_new_version(monkeypatch) -> None:
    json_version, binary_version = _version("json", "old host"), _version("binary", "new host")
    loaded = [json_version]
    manager = SimpleNamespace(loaded_manifest=lambda _name: loaded[0][0], host=lambda _name: loaded[0][1])
    reads = []

    async def body() -> bytes:
        reads.append(loaded[0][1])
        if len(reads) == 1:
            # The plugin directory changes while this request's body is still arriving.
            loaded[0] = binary_version
        return b'{"a": 1}'

    request = SimpleNamespace(
        method="POST",
        headers={},
        query_params=QueryParams(""),
        body=body,
        app=SimpleNamespace(state=SimpleNamespace(plugin_manager=manager, plugin_api_max_body_bytes=1 << 20)),
    )
    calls = []

    async def call(_request, host, _kind, **arguments):
        calls.append((host, arguments["body"], arguments["payload"]["route"]))
        return ("none",)

    monkeypatch.setattr(server_app, "_call_plugin_host", call)
    response = asyncio.run(server_app.call_plugin_api("notes", "upload", cast(Any, request)))

    # The handler that takes bytes gets bytes from its own version's host, not the JSON the old route decoded.
    assert response.status_code == 204
    assert calls == [("new host", b'{"a": 1}', "/upload")]
    assert reads == ["old host", "new host"]
