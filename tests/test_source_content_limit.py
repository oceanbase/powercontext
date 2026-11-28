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

"""Deployment-configured Content Source size limits and their discovery contract."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.limits import MAX_SOURCE_CONTENT_CHARS, MAX_SOURCE_CONTENT_CHARS_CEILING
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def _app(tmp_path: Path, *, max_chars: int | None = None):
    options: dict[str, Any] = {} if max_chars is None else {"source_content_max_chars": max_chars}
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            mcp=McpConfig(enabled=False),
            **options,
        )
    )


def _create_scope(client: TestClient, key: str = "content-limit") -> str:
    response = client.post(
        "/v1/scopes",
        json={"title": "content limit", "summary": "size limit contract", "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def _capture(client: TestClient, scope_id: str, source_id: str, content: str):
    return client.post(
        "/v1/sources/content",
        json={"scope_id": scope_id, "source_id": source_id, "content": content},
    )


def test_default_limit_is_enforced_above_the_documented_bound(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope_id = _create_scope(client)
        accepted = _capture(client, scope_id, "within-limit", "x" * MAX_SOURCE_CONTENT_CHARS)
        rejected = _capture(client, scope_id, "over-limit", "x" * (MAX_SOURCE_CONTENT_CHARS + 1))

    assert accepted.status_code == 202, accepted.text
    assert rejected.status_code == 422, rejected.text
    assert f"{MAX_SOURCE_CONTENT_CHARS}-character" in rejected.text


def test_configured_limit_replaces_the_default(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, max_chars=1_000)) as client:
        scope_id = _create_scope(client)
        accepted = _capture(client, scope_id, "at-limit", "x" * 1_000)
        rejected = _capture(client, scope_id, "over-limit", "x" * 1_001)

    assert accepted.status_code == 202, accepted.text
    assert rejected.status_code == 422, rejected.text
    assert "1000-character" in rejected.text


def test_configured_limit_above_the_default_accepts_larger_content(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, max_chars=300_000)) as client:
        scope_id = _create_scope(client)
        accepted = _capture(client, scope_id, "larger", "x" * 250_000)

    assert accepted.status_code == 202, accepted.text


def test_source_limits_endpoint_reports_the_effective_limit(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        default = client.get("/v1/source-limits")
    with TestClient(_app(tmp_path, max_chars=1_234)) as client:
        configured = client.get("/v1/source-limits")

    assert default.status_code == 200, default.text
    assert default.json()["source_content_max_chars"] == MAX_SOURCE_CONTENT_CHARS
    assert default.json()["source_content_max_chars_ceiling"] == MAX_SOURCE_CONTENT_CHARS_CEILING
    assert configured.status_code == 200, configured.text
    assert configured.json()["source_content_max_chars"] == 1_234


def test_batch_and_working_notes_share_the_limit(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, max_chars=10)) as client:
        scope_id = _create_scope(client)
        batch = client.post(
            "/v1/sources/content/batch",
            json={
                "scope_id": scope_id,
                "sources": [
                    {"source_id": "first", "content": "0123456789"},
                    {"source_id": "second", "content": "0123456789x"},
                ],
            },
        )
        note = client.post(
            "/v1/sources/working-notes",
            json={
                "scope_id": scope_id,
                "source_id": "note",
                "content": "0123456789x",
                "session_id": "session",
                "note_kind": "validation",
            },
        )

    assert batch.status_code == 422, batch.text
    errors = batch.json()["error"]["details"]["errors"]
    assert ["body", "sources", 1, "content"] in [item["loc"] for item in errors]
    assert note.status_code == 422, note.text


def test_lowering_the_limit_keeps_existing_sources_readable(tmp_path: Path) -> None:
    content = "x" * 250_000
    with TestClient(_app(tmp_path, max_chars=300_000)) as client:
        scope_id = _create_scope(client)
        assert _capture(client, scope_id, "large-source", content).status_code == 202

    with TestClient(_app(tmp_path, max_chars=100)) as client:
        detail = client.get(f"/v1/scopes/{scope_id}/sources/content/large-source")
        listing = client.get(f"/v1/scopes/{scope_id}/sources")
        accepted = _capture(client, scope_id, "small-source", "x" * 100)
        rejected = _capture(client, scope_id, "too-large", "x" * 101)

    assert detail.status_code == 200, detail.text
    assert detail.json()["content"] == content
    assert listing.status_code == 200, listing.text
    assert any(item["source_id"] == "large-source" and item["content"] == content for item in listing.json()["items"])
    assert accepted.status_code == 202, accepted.text
    assert rejected.status_code == 422, rejected.text


def test_over_limit_rejections_report_validation_telemetry(tmp_path: Path, caplog) -> None:
    """A deployment-limit rejection is a 422 in logs and spans, never an internal failure."""
    caplog.set_level(logging.INFO)

    with TestClient(_app(tmp_path, max_chars=5)) as client:
        scope_id = _create_scope(client)
        single = _capture(client, scope_id, "single", "x" * 6)
        batch = client.post(
            "/v1/sources/content/batch",
            json={"scope_id": scope_id, "sources": [{"source_id": "batch", "content": "x" * 6}]},
        )
        note = client.post(
            "/v1/sources/working-notes",
            json={
                "scope_id": scope_id,
                "source_id": "note",
                "content": "x" * 6,
                "session_id": "session",
                "note_kind": "validation",
            },
        )
        created = _create_source(client, scope_id, "x" * 6)

    assert single.status_code == batch.status_code == note.status_code == created.status_code == 422
    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "application.operation.completed"
        and getattr(record, "outcome", None) == "failure"
    ]
    assert len(failures) == 4
    assert {getattr(record, "status_code", None) for record in failures} == {422}
    assert {getattr(record, "error_code", None) for record in failures} == {"invalid_request"}
    assert not any(getattr(record, "error_code", None) == "internal_error" for record in caplog.records)


def test_schema_ceiling_rejects_beyond_the_storage_bound(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, max_chars=MAX_SOURCE_CONTENT_CHARS_CEILING)) as client:
        scope_id = _create_scope(client)
        rejected = _capture(client, scope_id, "beyond-ceiling", "x" * (MAX_SOURCE_CONTENT_CHARS_CEILING + 1))

    assert rejected.status_code == 422, rejected.text


def _create_source(client: TestClient, scope_id: str, content):
    return client.post(
        f"/v1/scopes/{scope_id}/sources",
        json={"source_type": "content", "content": content},
    )


def test_create_source_enforces_the_limit_for_string_content(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, max_chars=10)) as client:
        scope_id = _create_scope(client)
        accepted = _create_source(client, scope_id, "x" * 10)
        rejected = _create_source(client, scope_id, "x" * 11)

    assert accepted.status_code == 201, accepted.text
    assert rejected.status_code == 422, rejected.text
    errors = rejected.json()["error"]["details"]["errors"]
    assert ["body", "content"] in [item["loc"] for item in errors]


def test_create_source_measures_non_string_content_by_canonical_length(tmp_path: Path) -> None:
    # RFC 8785 canonical text for {"a": "xxxxxxxxxxxx"} is exactly 20 characters.
    with TestClient(_app(tmp_path, max_chars=20)) as client:
        scope_id = _create_scope(client)
        accepted = _create_source(client, scope_id, {"a": "x" * 12})
        rejected = _create_source(client, scope_id, {"a": "x" * 13})

    assert accepted.status_code == 201, accepted.text
    assert rejected.status_code == 422, rejected.text
