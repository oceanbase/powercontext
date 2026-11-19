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

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.http import CaptureContentSourcesRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

BATCH = "/v1/sources/content/batch"


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'batch.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as started:
        yield started


@pytest.fixture
def scope(client: TestClient) -> str:
    created = client.post("/v1/scopes", json={"title": "batch", "summary": "batch", "idempotency_key": "batch"})
    return created.json()["scope_id"]


def _parts(*contents: str) -> list[dict[str, object]]:
    return [
        {"source_id": f"turn-{index}of{len(contents)}", "content": content, "metadata": {"part": index}}
        for index, content in enumerate(contents, start=1)
    ]


def _stored(client: TestClient, scope: str, source_id: str) -> int:
    return client.get(f"/v1/scopes/{scope}/sources/content/{source_id}").status_code


def test_a_batch_is_stored_in_request_order_and_replays(client: TestClient, scope: str) -> None:
    body = {"scope_id": scope, "sources": _parts("first", "second", "third")}

    response = client.post(BATCH, json=body)

    assert response.status_code == 202
    receipts = response.json()["sources"]
    assert [item["source"]["source_id"] for item in receipts] == ["turn-1of3", "turn-2of3", "turn-3of3"]
    first = receipts[0]["position"]
    assert [item["position"] for item in receipts] == [first, first + 1, first + 2]
    assert client.post(BATCH, json=body).json() == response.json()


def test_a_conflicting_item_stores_nothing_from_the_batch(client: TestClient, scope: str) -> None:
    single = {"scope_id": scope, "source_id": "turn-2of3", "content": "stored earlier"}
    assert client.post("/v1/sources/content", json=single).status_code == 202

    response = client.post(BATCH, json={"scope_id": scope, "sources": _parts("first", "different", "third")})

    assert response.status_code == 409
    assert _stored(client, scope, "turn-1of3") == 404
    assert _stored(client, scope, "turn-3of3") == 404


def test_an_item_already_stored_with_the_same_content_counts_as_stored(client: TestClient, scope: str) -> None:
    single = {"scope_id": scope, "source_id": "turn-1of2", "content": "first", "metadata": {"part": 1}}
    earlier = client.post("/v1/sources/content", json=single).json()

    response = client.post(BATCH, json={"scope_id": scope, "sources": _parts("first", "second")})

    assert response.status_code == 202
    assert response.json()["sources"][0]["position"] == earlier["position"]
    assert _stored(client, scope, "turn-2of2") == 200


def test_an_item_already_stored_with_other_metadata_rejects_the_batch(client: TestClient, scope: str) -> None:
    single = {"scope_id": scope, "source_id": "turn-1of2", "content": "first", "metadata": {"part": 1, "parts": 1}}
    assert client.post("/v1/sources/content", json=single).status_code == 202

    response = client.post(BATCH, json={"scope_id": scope, "sources": _parts("first", "second")})

    assert response.status_code == 409
    assert _stored(client, scope, "turn-2of2") == 404


@pytest.mark.parametrize(
    "sources",
    [
        [],
        [{"source_id": "same", "content": "a"}, {"source_id": "same", "content": "b"}],
        [{"source_id": "ok", "content": "a"}, {"source_id": "blank", "content": "  "}],
        [{"source_id": "note", "content": "a", "metadata": {"kind": "working_note"}}],
    ],
    ids=["empty", "duplicate-id", "blank-content", "working-note"],
)
def test_an_invalid_batch_is_rejected_without_storing(
    client: TestClient, scope: str, sources: list[dict[str, object]]
) -> None:
    response = client.post(BATCH, json={"scope_id": scope, "sources": sources})

    assert response.status_code == 422
    assert _stored(client, scope, "ok") == 404


def test_capabilities_report_the_batch_limit_of_the_contract(client: TestClient) -> None:
    limits = client.get("/v1/capabilities").json()["context_protection"]["limits"]
    contract = CaptureContentSourcesRequest.model_json_schema()["properties"]["sources"]

    assert limits["capture_batch_sources"] == contract["maxItems"]
