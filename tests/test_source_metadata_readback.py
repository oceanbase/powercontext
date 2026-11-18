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

"""HTTP readback of caller-supplied metadata for Content Sources."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

METADATA = {
    "account_id": "example-enterprise",
    "call_id": "example-call-001",
    "subject_type": "customer",
    "nested": {"tags": ["a", "b"], "flags": [True, False, None], "depth": {"ratio": 1.5}},
    "unicode": "示例客户",
}
CONTENT = "The customer requested a callback."


def _app(tmp_path: Path):
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            mcp=McpConfig(enabled=False),
        )
    )


def _create_scope(client: TestClient, key: str = "metadata-scope") -> str:
    response = client.post(
        "/v1/scopes",
        json={"title": "metadata", "summary": "readback", "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def _capture(
    client: TestClient,
    scope_id: str,
    *,
    source_id: str = "example-call-001",
    metadata: dict[str, object] | None = METADATA,
    content: str = CONTENT,
) -> None:
    body: dict[str, object] = {"scope_id": scope_id, "source_id": source_id, "content": content}
    if metadata is not None:
        body["metadata"] = metadata
    response = client.post("/v1/sources/content", json=body)
    assert response.status_code == 202, response.text


def _detail(client: TestClient, scope_id: str, source_id: str = "example-call-001") -> dict[str, object]:
    response = client.get(f"/v1/scopes/{scope_id}/sources/content/{source_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _list(client: TestClient, scope_id: str, **params: object) -> dict[str, object]:
    response = client.get(f"/v1/scopes/{scope_id}/sources", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_detail_and_list_return_the_captured_metadata(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        _capture(client, scope_id)

        detail = _detail(client, scope_id)
        page = _list(client, scope_id)

    assert detail["metadata"] == METADATA
    assert page["items"][0]["metadata"] == METADATA
    assert page["items"][0]["metadata"] == detail["metadata"]
    assert detail["content"] == CONTENT
    assert page["items"][0]["content"] == CONTENT


def test_sources_without_metadata_report_an_empty_object(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        _capture(client, scope_id, metadata=None)

        detail = _detail(client, scope_id)
        page = _list(client, scope_id)

    assert detail["metadata"] == {}
    assert page["items"][0]["metadata"] == {}


def test_legacy_payload_without_metadata_reports_an_empty_object(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        _capture(client, scope_id)

    # Remove the stored field to model a Source written before metadata was persisted.
    connection = sqlite3.connect(tmp_path / "runtime.db")
    try:
        payload = connection.execute("select payload from pc_sources where source_id = 'example-call-001'").fetchone()[
            0
        ]
        document = json.loads(payload)
        document["value"].pop("metadata", None)
        connection.execute(
            "update pc_sources set payload = ? where source_id = 'example-call-001'",
            (json.dumps(document).encode("utf-8"),),
        )
        connection.commit()
    finally:
        connection.close()

    with TestClient(_app(tmp_path)) as client:
        detail = _detail(client, scope_id)
        page = _list(client, scope_id)

    assert detail["metadata"] == {}
    assert page["items"][0]["metadata"] == {}


def test_metadata_survives_reopening_the_database(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        _capture(client, scope_id)

    with TestClient(_app(tmp_path)) as client:
        detail = _detail(client, scope_id)
        page = _list(client, scope_id)

    assert detail["metadata"] == METADATA
    assert page["items"][0]["metadata"] == METADATA


def test_list_pages_preserve_metadata_and_cursor_order(tmp_path: Path) -> None:
    identifiers = ("metadata-a", "metadata-b", "metadata-c")
    metadata = {source_id: {"source": source_id, "index": index} for index, source_id in enumerate(identifiers)}

    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        for source_id in identifiers:
            _capture(client, scope_id, source_id=source_id, metadata=metadata[source_id])

        first = _list(client, scope_id, limit=2)
        assert first["next_cursor"] is not None
        second = _list(client, scope_id, limit=2, cursor=first["next_cursor"])

    assert [item["source_id"] for item in first["items"]] == list(identifiers[:2])
    assert [item["metadata"] for item in first["items"]] == [metadata[i] for i in identifiers[:2]]
    assert [item["source_id"] for item in second["items"]] == [identifiers[2]]
    assert second["items"][0]["metadata"] == metadata[identifiers[2]]
    assert second["next_cursor"] is None


def test_large_metadata_counts_toward_the_page_budget(tmp_path: Path) -> None:
    # The Source page budget is 4 MiB; each record stays below it while the pair does not.
    large_metadata = {"payload": "x" * (2 * 1024 * 1024)}

    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        _capture(client, scope_id, source_id="large-a", metadata=large_metadata)
        _capture(client, scope_id, source_id="large-b", metadata=large_metadata)

        first = _list(client, scope_id, limit=10)
        assert first["next_cursor"] is not None
        second = _list(client, scope_id, limit=10, cursor=first["next_cursor"])

    assert [item["source_id"] for item in first["items"]] == ["large-a"]
    assert first["items"][0]["metadata"] == large_metadata
    assert [item["source_id"] for item in second["items"]] == ["large-b"]


def test_metadata_does_not_change_content_digest_or_identity(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        _capture(client, scope_id, source_id="with-metadata", metadata={"account_id": "acme"})
        _capture(client, scope_id, source_id="without-metadata", metadata=None)

        with_metadata = _detail(client, scope_id, "with-metadata")
        without_metadata = _detail(client, scope_id, "without-metadata")

        # Re-capturing an existing Source identity keeps rejecting the duplicate, and the
        # original payload (including metadata) is unchanged.
        conflict = client.post(
            "/v1/sources/content",
            json={
                "scope_id": scope_id,
                "source_id": "with-metadata",
                "content": CONTENT,
                "metadata": {"account_id": "changed"},
            },
        )
        assert conflict.status_code == 409, conflict.text
        recaptured = _detail(client, scope_id, "with-metadata")

    assert with_metadata["content_digest"] == without_metadata["content_digest"]
    assert with_metadata["position"] == 1
    assert without_metadata["position"] == 2
    assert recaptured["metadata"] == {"account_id": "acme"}
    assert recaptured["content_digest"] == with_metadata["content_digest"]
