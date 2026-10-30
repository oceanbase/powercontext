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

"""The reference external adapter honors Core decisions at actual model I/O."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

import httpx
import pytest
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream
from pydantic import SecretStr

from examples.context_protection.external_executor import ExecutionRefused, TrustedTarget, execute_checked_json
from powercontext.builtin.runtime import InferenceConfig
from powercontext.builtin.runtime import context_check as context_check_runtime
from powercontext.client import PowerContextClient
from powercontext.http import ModelJsonPayload, PrepareContextRequest, PreparedContextLocation, PreparedContextWithReuse
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from tests.e2e.server_storage import prepare_server_database
from tests.e2e.test_access_control_regressions import _scope, _server
from tests.e2e.test_context_check_authorization import _inference
from tests.e2e.test_context_protection_transports import _ADMIN, _Authentication, _create_scope, _send, _settings
from tests.e2e.test_rfc2086_transport_acceptance import RecordingHandler, upstream

_SECRET = "synthetic-transport-secret"  # noqa: S105 - deliberately forbidden synthetic fixture


def _response(text: str) -> bytes:
    return (
        json.dumps(
            {
                "model": "test",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode()


def test_external_executor_preserves_approved_bytes_and_withholds_blocked_content(tmp_path):
    safe_output = _response("继续使用端口 8123。保留字面模板 {{PROJECT_abcdefgh}}。不推测其真实值。")

    async def scenario(server):
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        settings = _settings(tmp_path)
        profile = settings.inference.protection.targets["model"].model_copy(update={"endpoint": endpoint})
        settings = settings.model_copy(
            update={
                "inference": InferenceConfig(
                    protection=(settings.inference.protection.model_copy(update={"targets": {"model": profile}}))
                )
            }
        )
        await asyncio.to_thread(prepare_server_database, settings)
        async with open_builtin_access_control(
            settings.database,
            bootstrap_administrators=(_ADMIN,),
            deployment_id=settings.access.deployment_id,
        ):
            pass
        app = create_server_app(
            settings=settings,
            authentication_provider=_Authentication(),
            scheduler_path=tmp_path / "scheduler.db",
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer " + _ADMIN.id},
            ) as http,
        ):
            scope = await _create_scope(http, "executor-scope")
            grant = await http.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": _ADMIN.type, "id": _ADMIN.id},
                    "resource": {"type": "scope", "scope_id": scope},
                    "role": "scope.contributor",
                    "idempotency_key": "executor-contributor",
                },
            )
            assert grant.status_code == 201, grant.text
            remembered = await http.post(
                "/v1/memory/remember",
                json={
                    "scope_id": scope,
                    "kind": "fact",
                    "text": "Alpha port stays at 8123. The literal project template is {{PROJECT_abcdefgh}}.",
                },
            )
            assert remembered.status_code == 200, remembered.text
            client = PowerContextClient("http://localhost", token=_ADMIN.id, http_client=http)
            prepared = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": scope,
                    "query": "alpha",
                    "include_reuse": True,
                    "assembly": {"sections": [{"family": "memory", "limit": 8}]},
                })
            )
            assert isinstance(prepared, PreparedContextWithReuse)
            assert prepared.context.content is not None
            assert "{{PROJECT_abcdefgh}}" in prepared.context.content
            send = _send(scope, prepared)
            payload = ModelJsonPayload.model_validate(send["payload"])
            locations = tuple(PreparedContextLocation.model_validate(item) for item in send["prepared_contexts"])
            target = TrustedTarget(
                profile_id="model",
                endpoint=endpoint,
                bearer=SecretStr("separate-upstream-fixture-credential"),
            )
            released: list[bytes] = []

            async def invoke(value):
                result = await execute_checked_json(
                    powercontext=client,
                    target=target,
                    scope_id=scope,
                    payload=value,
                    prepared_contexts=locations,
                )
                released.append(result)
                return result

            assert await invoke(payload) == safe_output
            assert server.received == [payload.body.encode()]
            assert released == [safe_output]

            blocked_body = json.loads(payload.body)
            blocked_body["messages"][0]["content"] += _SECRET
            blocked_input = payload.model_copy(update={"body": json.dumps(blocked_body)})
            with pytest.raises(ExecutionRefused, match="context_admission_refused") as blocked:
                await invoke(blocked_input)
            assert _SECRET not in str(blocked.value)
            assert server.received == [payload.body.encode()]
            assert released == [safe_output]

            server.body = _response(_SECRET)
            with pytest.raises(ExecutionRefused, match="context_admission_refused") as blocked:
                await invoke(payload)
            assert _SECRET not in str(blocked.value)
            assert server.received == [payload.body.encode()] * 2
            assert released == [safe_output]

            server.status = 500
            server.body = json.dumps({"error": {"message": "private upstream diagnostic"}}).encode()
            with pytest.raises(ExecutionRefused, match="upstream_http_error") as failed:
                await invoke(payload)
            assert "private upstream diagnostic" not in str(failed.value)
            assert len(server.received) == 3
            assert released == [safe_output]

    with upstream(safe_output) as server:
        asyncio.run(scenario(server))


@pytest.mark.parametrize("delay_phase", ["connect", "write", "response"])
def test_external_executor_bounds_send_work_without_expiring_an_on_time_response(tmp_path, monkeypatch, delay_phase):
    monkeypatch.setattr(context_check_runtime, "SEND_VALIDITY", timedelta(seconds=1))
    if delay_phase == "response":
        original_post = RecordingHandler.do_POST

        def delayed_response(handler):
            time.sleep(2)
            original_post(handler)

        monkeypatch.setattr(RecordingHandler, "do_POST", delayed_response)
    else:
        owner, method = (AnyIOBackend, "connect_tcp") if delay_phase == "connect" else (AnyIOStream, "write")
        original = getattr(owner, method)
        delayed = False

        async def delayed_send(*args, **kwargs):
            nonlocal delayed
            if not delayed:
                delayed = True
                await asyncio.sleep(2)
            return await original(*args, **kwargs)

        monkeypatch.setattr(owner, method, delayed_send)

    safe_output = _response("The response may arrive after the send permit expires.")

    async def scenario(server):
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        inference = _inference()
        target = inference.protection.targets["model"].model_copy(update={"endpoint": endpoint})
        inference = inference.model_copy(
            update={"protection": inference.protection.model_copy(update={"targets": {"model": target}})}
        )
        async with _server(tmp_path, inference=inference) as (_, http, _):
            scope_id = await _scope(http)
            client = PowerContextClient("http://localhost", http_client=http)
            payload = ModelJsonPayload.model_validate({
                "schema": "powercontext.model-json-payload.v1",
                "headers": {"content-type": "application/json"},
                "body": json.dumps({
                    "model": "test",
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": "safe"}],
                }),
            })

            async def execute():
                return await execute_checked_json(
                    powercontext=client,
                    target=TrustedTarget(profile_id="model", endpoint=endpoint, timeout_seconds=10),
                    scope_id=scope_id,
                    payload=payload,
                )

            if delay_phase == "response":
                assert await execute() == safe_output
                assert server.received == [payload.body.encode()]
            else:
                with pytest.raises(ExecutionRefused, match="execution_unavailable_or_uncertain"):
                    await execute()
                assert server.received == []

    with upstream(safe_output) as server:
        asyncio.run(scenario(server))
