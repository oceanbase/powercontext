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

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from powercontext.client import (
    ForbiddenResponseError,
    InvalidResponseError,
    PowerContextClient,
    ServerResponseError,
    TransportError,
    UnauthorizedResponseError,
    UnavailableResponseError,
)
from powercontext.client.settings import ClientSettings
from powercontext.http import (
    AccessAction,
    AccessArtifactIdentity,
    AccessBindingReplacementInput,
    AccessCheckRequest,
    AccessCheckRequirement,
    AccessPrincipal,
    AccessRequirementMatch,
    AccessResource,
    AccessSubject,
    ArtifactAccessResource,
    CaptureContentSourceRequest,
    ExactScopeSelection,
    GetHandoffReportRequest,
    ListArtifactsRequest,
    ReplaceAccessBindingRequest,
    ReplaceArtifactRequest,
    ReplaceMemoryArtifactContent,
    ReplaceMemoryArtifactEntry,
    ReplaceMemoryArtifactRequest,
    ReportFormat,
    ScopeId,
    ScopeSelection,
    UpdateScopeRequest,
)


def test_client_exposes_typed_access_check() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "allowed": True,
                    "decisions": [{"allowed": True, "reason_code": "role-binding"}],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            decision = await client.check_access(
                AccessCheckRequest(
                    match=AccessRequirementMatch.ALL,
                    requirements=[
                        AccessCheckRequirement(
                            action=AccessAction.ARTIFACT_READ,
                            resource=AccessResource(
                                root=ArtifactAccessResource(
                                    type="artifact",
                                    scope_id="scope-a",
                                    identity=AccessArtifactIdentity(family="handoff", artifact_id="handoff-a"),
                                    selector=None,
                                )
                            ),
                        )
                    ],
                )
            )

        assert decision.allowed is True
        assert requests[0].url.path == "/v1/access/check"
        payload = json.loads(requests[0].content)
        assert payload["requirements"][0]["resource"]["identity"]["artifact_id"] == "handoff-a"

    asyncio.run(scenario())


def test_client_exposes_typed_access_binding_replacement() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []
        previous = {
            "binding_id": "binding-bob",
            "subject": {"type": "user", "id": "bob", "description": None},
            "resource": {"type": "scope", "scope_id": "scope-a"},
            "role": "scope.viewer",
            "granted_by": {"type": "service", "id": "admin", "description": None},
            "reason": None,
            "created_at": "2026-09-03T00:00:00Z",
            "expires_at": None,
            "state": "revoked",
            "version": 2,
            "policy_revision": "2",
            "idempotency_key": "create-bob",
            "revoked_at": "2026-09-03T01:00:00Z",
            "revoked_by": {"type": "service", "id": "admin", "description": None},
        }
        current = previous | {
            "binding_id": "binding-alice",
            "subject": {"type": "user", "id": "alice", "description": None},
            "reason": "transfer",
            "created_at": "2026-09-03T01:00:00Z",
            "state": "active",
            "version": 1,
            "idempotency_key": "replace-with-alice",
            "revoked_at": None,
            "revoked_by": None,
        }

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"previous": previous, "current": current})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            replacement = await client.replace_access_binding(
                ReplaceAccessBindingRequest(
                    binding_id="binding-bob",
                    expected_version=1,
                    replacement=AccessBindingReplacementInput(
                        subject=AccessSubject(root=AccessPrincipal(type="user", id="alice")),
                        reason="transfer",
                    ),
                    idempotency_key="replace-with-alice",
                )
            )

        assert replacement.previous.state.value == "revoked"
        assert replacement.current.subject.root.id == "alice"
        assert requests[0].url.path == "/v1/access/bindings/replace"
        assert json.loads(requests[0].content)["replacement"]["subject"]["id"] == "alice"

    asyncio.run(scenario())


def test_client_rejects_an_undeclared_success_status() -> None:
    async def scenario() -> None:
        response = httpx.Response(
            200,
            json={
                "status": "accepted",
                "source": {"name": "content", "source_id": "turn-1"},
                "position": 1,
            },
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(ServerResponseError) as caught:
                await client.capture_content_source(
                    CaptureContentSourceRequest(scope_id="project", source_id="turn-1", content="content")
                )

        assert caught.value.status_code == 200

    asyncio.run(scenario())


def test_client_preserves_server_error_context() -> None:
    async def scenario() -> None:
        response = httpx.Response(
            503,
            headers={"X-PowerContext-Request-ID": "request-123"},
            json={
                "error": {
                    "code": "runtime_not_ready",
                    "message": "The Runtime is not ready.",
                    "details": {"component": "memory"},
                }
            },
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(ServerResponseError) as caught:
                await client.get_readiness()

        assert caught.value.status_code == 503
        assert caught.value.request_id == "request-123"
        assert caught.value.code == "runtime_not_ready"
        assert caught.value.server_message == "The Runtime is not ready."
        assert caught.value.details == {"component": "memory"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, UnauthorizedResponseError),
        (403, ForbiddenResponseError),
        (503, UnavailableResponseError),
    ],
)
def test_client_maps_access_statuses_to_distinct_stable_exceptions(
    status_code: int,
    error_type: type[ServerResponseError],
) -> None:
    async def scenario() -> None:
        response = httpx.Response(
            status_code,
            json={"error": {"code": "access_failure", "message": "Access failed.", "details": None}},
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            with pytest.raises(error_type) as caught:
                await client.get_readiness()
        assert caught.value.status_code == status_code

    asyncio.run(scenario())


def test_client_sends_an_explicit_bearer_token() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"status": "ok"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient(
                "https://memory.example",
                token="secret-token",  # noqa: S106 - non-secret test credential.
                http_client=http_client,
            )
            await client.get_liveness()

        assert len(requests) == 1
        assert requests[0].headers["Authorization"] == "Bearer secret-token"

    asyncio.run(scenario())


def test_client_uses_scope_resource_paths_without_repeating_the_id_in_the_body() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "scope_id": "scope:feature",
                    "title": "Feature",
                    "summary": "Current work",
                    "parent_scope_id": None,
                    "context_references": [],
                    "external_references": [],
                    "version": 1,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            await client.get_scope("scope:feature")
            await client.update_scope(
                "scope:feature",
                UpdateScopeRequest(
                    expected_version=1,
                    title="Feature",
                    summary="Current work",
                ),
            )

        assert [request.method for request in requests] == ["GET", "PUT"]
        assert [request.url.raw_path for request in requests] == [
            b"/v1/scopes/scope%3Afeature",
            b"/v1/scopes/scope%3Afeature",
        ]
        assert requests[0].content == b""
        assert "scope_id" not in json.loads(requests[1].content)

    asyncio.run(scenario())


def test_client_keeps_a_generic_server_error_when_the_error_body_is_invalid() -> None:
    async def scenario() -> None:
        response = httpx.Response(500, text="Internal Server Error")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(ServerResponseError) as caught:
                await client.get_liveness()

        assert caught.value.status_code == 500
        assert caught.value.code is None

    asyncio.run(scenario())


def test_client_rejects_an_invalid_success_response() -> None:
    async def scenario() -> None:
        response = httpx.Response(
            200,
            headers={"X-PowerContext-Request-ID": "request-123"},
            json={"status": "ok", "unexpected": True},
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(InvalidResponseError) as caught:
                await client.get_liveness()

        assert caught.value.request_id == "request-123"

    asyncio.run(scenario())


def test_client_wraps_http_transport_failures() -> None:
    async def fail(request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message, request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)

            with pytest.raises(TransportError) as caught:
                await client.get_liveness()

        assert caught.value.path == "/health/live"
        assert isinstance(caught.value.__cause__, httpx.ConnectError)

    asyncio.run(scenario())


def test_client_downloads_handoff_report_bytes_and_sets_download_flag() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=b"# Handoff Report\n", headers={"content-type": "text/markdown"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            request = GetHandoffReportRequest(
                selection=ScopeSelection(root=ExactScopeSelection(mode="exact", scope_ids=[ScopeId("scope-1")])),
                format=ReportFormat.MARKDOWN,
            )
            rendered = await client.get_handoff_report(request)
            content = await client.download_handoff_report(request)

        assert rendered == "# Handoff Report\n"
        assert content == b"# Handoff Report\n"
        assert len(requests) == 2
        assert json.loads(requests[0].content)["download"] is False
        assert json.loads(requests[0].content)["selection"] == {
            "mode": "exact",
            "scope_ids": ["scope-1"],
        }
        assert json.loads(requests[1].content)["download"] is True

    asyncio.run(scenario())


def test_client_serializes_scoped_artifact_paths_and_list_query() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "scope_id": "scope one",
                            "family": "memory",
                            "artifact_id": "memory-1",
                            "revision": 1,
                            "sources": [],
                            "artifacts": [],
                            "content_digest": f"sha256:{'0' * 64}",
                        }
                    ],
                    "next_cursor": None,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            page = await client.list_artifacts(
                "scope one",
                "memory",
                ListArtifactsRequest(limit=7, cursor="cursor-1"),
            )

        assert [item.artifact_id for item in page.items] == ["memory-1"]
        assert len(requests) == 1
        assert requests[0].url.path == "/v1/scopes/scope one/artifacts/memory"
        assert dict(requests[0].url.params) == {"limit": "7", "cursor": "cursor-1"}

    asyncio.run(scenario())


def test_client_decodes_declared_not_modified_without_a_body() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(304, headers={"ETag": '"revision:2"'})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            artifact = await client.get_artifact(
                "scope-a",
                "document",
                "artifact-1",
                if_none_match='"revision:2"',
            )

        assert artifact is None
        assert len(requests) == 1
        assert requests[0].url.path == "/v1/scopes/scope-a/artifacts/document/artifact-1"
        assert requests[0].headers["If-None-Match"] == '"revision:2"'

    asyncio.run(scenario())


def test_client_sends_opaque_replace_precondition_verbatim() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "scope_id": "scope-a",
                    "family": "memory",
                    "artifact_id": "artifact-1",
                    "revision": 4,
                    "content": {"manifest": {}},
                    "sources": [],
                    "artifacts": [],
                    "content_digest": f"sha256:{'0' * 64}",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            result = await client.replace_artifact(
                "scope-a",
                "memory",
                "artifact-1",
                ReplaceArtifactRequest(
                    root=ReplaceMemoryArtifactRequest(
                        content=ReplaceMemoryArtifactContent(
                            entries=[ReplaceMemoryArtifactEntry(kind="preference", text="Use Chinese")]
                        )
                    )
                ),
                expected_etag='"opaque-v4"',
            )

        assert result.revision == 4
        assert len(requests) == 1
        assert requests[0].method == "PUT"
        assert requests[0].url.path == "/v1/scopes/scope-a/artifacts/memory/artifact-1"
        assert requests[0].headers["If-Match"] == '"opaque-v4"'

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "server_url",
    [
        "https://user:password@memory.example",
        "https://memory.example/api?token=secret",
        "https://memory.example/api#fragment",
    ],
)
def test_client_settings_reject_ambiguous_or_sensitive_server_urls(server_url: str) -> None:
    with pytest.raises(ValidationError):
        ClientSettings(server_url=server_url)


def test_client_settings_error_repr_does_not_leak_url_credentials() -> None:
    with pytest.raises(ValidationError) as caught:
        ClientSettings(server_url="https://user:do-not-log@memory.example")

    assert "do-not-log" not in repr(caught.value)


def test_client_settings_hide_the_api_token(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "secret-token")

    settings = ClientSettings()

    assert settings.api_token is not None
    assert settings.api_token.get_secret_value() == "secret-token"
    assert "secret-token" not in repr(settings)
