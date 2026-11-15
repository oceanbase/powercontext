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

"""The OpenAPI document of a plugin's routes resolves every model reference."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from powercontext.builtin.plugins.manifest import PluginManifest
from powercontext.plugins import ArtifactPluginSpec, PluginApiSpec, RouteSpec
from powercontext.server.plugins import plugin_openapi


class Child(BaseModel):
    text: str


class Note(BaseModel):
    child: Child


class Tag(BaseModel):
    child: Child


def _other_child() -> type[BaseModel]:
    class Child(BaseModel):
        count: int

    class Counter(BaseModel):
        child: Child

    return Counter


def _route(path: str, model: type[BaseModel]) -> RouteSpec:
    return RouteSpec(
        method="POST",
        path=path,
        handler=path.strip("/"),
        access="authenticated",
        body="json",
        request_schema=model.model_json_schema(),
        response_schema=model.model_json_schema(),
    )


def _schema(document, path: str) -> dict[str, Any]:
    return document["paths"][f"/v1/plugins/docs/api{path}"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]


def test_routes_share_a_model_component_and_a_different_model_of_the_same_name_gets_its_own():
    routes = (_route("/note", Note), _route("/tag", Tag), _route("/counter", _other_child()))
    manifest = PluginManifest(
        name="docs", spec=ArtifactPluginSpec(family="docs-notes", api=PluginApiSpec(routes=routes))
    )

    document = plugin_openapi(manifest)
    schemas = document["components"]["schemas"]

    assert _schema(document, "/note")["properties"]["child"] == {"$ref": "#/components/schemas/Child"}
    assert _schema(document, "/tag")["properties"]["child"] == {"$ref": "#/components/schemas/Child"}
    assert _schema(document, "/counter")["properties"]["child"] == {"$ref": "#/components/schemas/Child2"}
    assert schemas["Child"]["properties"] == {"text": {"title": "Text", "type": "string"}}
    assert schemas["Child2"]["properties"] == {"count": {"title": "Count", "type": "integer"}}
    assert "$defs" not in _schema(document, "/note")
    assert {"Error", "Child", "Child2"} <= set(schemas)
