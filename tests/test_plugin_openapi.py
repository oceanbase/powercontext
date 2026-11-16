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

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

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


def _v1_request() -> type[BaseModel]:
    class Metadata(BaseModel):
        author: str

    class Document(BaseModel):
        metadata: Metadata

    class Request(BaseModel):
        document: Document

    return Request


def _v2_request() -> type[BaseModel]:
    class Metadata(BaseModel):
        authors: list[str]

    class Document(BaseModel):
        metadata: Metadata

    class Request(BaseModel):
        document: Document

    return Request


def _resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].removeprefix("#/components/schemas/")]
    return schema


def test_routes_with_same_named_models_of_different_shapes_each_keep_their_own():
    v1, v2 = _v1_request(), _v2_request()
    manifest = PluginManifest(
        name="docs",
        spec=ArtifactPluginSpec(family="docs-notes", api=PluginApiSpec(routes=(_route("/v1", v1), _route("/v2", v2)))),
    )

    document = plugin_openapi(manifest)

    def metadata(path: str) -> dict[str, Any]:
        request = _resolve(document, _schema(document, path))
        nested = _resolve(document, request["properties"]["document"])
        return _resolve(document, nested["properties"]["metadata"])

    assert metadata("/v1")["required"] == ["author"]
    assert metadata("/v2")["required"] == ["authors"]


class Cat(BaseModel):
    kind: Literal["cat"]
    lives: int


class Dog(BaseModel):
    kind: Literal["dog"]
    good: bool


class Owner(BaseModel):
    pet: Annotated[Cat | Dog, Field(discriminator="kind")]


def test_discriminator_mappings_point_at_document_components():
    manifest = PluginManifest(
        name="docs", spec=ArtifactPluginSpec(family="docs-notes", api=PluginApiSpec(routes=(_route("/owner", Owner),)))
    )

    document = plugin_openapi(manifest)
    pet = _schema(document, "/owner")["properties"]["pet"]

    assert pet["discriminator"]["mapping"] == {
        "cat": "#/components/schemas/Cat",
        "dog": "#/components/schemas/Dog",
    }
    assert "$defs" not in json.dumps(document)


def _v1_document_request() -> type[BaseModel]:
    class Document(BaseModel):
        title: str

    class Request(BaseModel):
        document: Document

    return Request


def _v2_document_request() -> type[BaseModel]:
    class Document(BaseModel):
        content: str

    class Document2(BaseModel):
        revision: int

    class Request(BaseModel):
        document: Document
        previous: Document2

    return Request


def test_new_component_names_do_not_collide_with_the_routes_own_models():
    routes = (_route("/v1", _v1_document_request()), _route("/v2", _v2_document_request()))
    manifest = PluginManifest(
        name="docs", spec=ArtifactPluginSpec(family="docs-notes", api=PluginApiSpec(routes=routes))
    )

    document = plugin_openapi(manifest)
    v2 = _resolve(document, _schema(document, "/v2"))

    assert _resolve(document, v2["properties"]["document"])["required"] == ["content"]
    assert _resolve(document, v2["properties"]["previous"])["required"] == ["revision"]
    v1 = _resolve(document, _schema(document, "/v1"))
    assert _resolve(document, v1["properties"]["document"])["required"] == ["title"]
