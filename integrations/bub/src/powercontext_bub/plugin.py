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

"""Inject prepared PowerContext into Bub model calls."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx
import republic
from bub import Settings, config, ensure_config, hookimpl
from bub.envelope import content_of, field_of
from bub.hooks.interception import LLMCallRequest, LLMCallResult, ToolCall, ToolCallResult
from bub.prompt import UserContent, prompt_text, to_content
from bub.turn import TurnState
from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from powercontext.client import InvalidResponseError, PowerContextClient, ServerResponseError, TransportError
from powercontext.client.capture import render_capture_event
from powercontext.client.transport_policy import ClientTransportSettings
from powercontext.http import CaptureContentSourceRequest, FlushMemoryRequest, PrepareContextRequest, PreparedContext

try:
    from powercontext.http import ContextAssembly
except ImportError:
    # Older core releases support legacy recall but cannot accept assembly settings.
    from types import NoneType as ContextAssembly

from .scope import resolve_scope_id, workspace_binding_key

STATE_KEY = "_powercontext"
CONTEXT_MARKER = "PowerContext host-supplied context"
CLIENT_ERRORS = (InvalidResponseError, ServerResponseError, TransportError)
# ``POST /v1/context/prepare`` bounds ``query`` at 8,192 characters. The byte bound is the hosts' own margin,
# shared with the hook-based hosts, so a recall also fits a server that measures bytes.
MAX_QUERY_CHARACTERS = 8192
MAX_QUERY_BYTES = 8192
_BUILDING_PROMPT: ContextVar[bool] = ContextVar("powercontext_bub_building_prompt", default=False)
GUIDANCE = """\
PowerContext provides durable project memory shared across agent sessions.
Relevant host-supplied context is injected automatically before each model call.
Use powercontext.search for follow-up recall beyond the injected context.
Use powercontext.remember when the user establishes a durable decision, preference, constraint, or procedure."""
CAPTURE_SCHEMA = "powercontext.bub-capture-event/v1"


@config(name="powercontext")
class PowerContextSettings(ClientTransportSettings, Settings):
    """Validated Bub configuration for the PowerContext plugin."""

    model_config = SettingsConfigDict(
        env_prefix="POWERCONTEXT_BUB_",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    transport_host: ClassVar[str] = "bub"
    base_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
    api_token: SecretStr | None = Field(default=None, repr=False)
    scope_id: str | None = Field(default=None, min_length=1)
    timeout: float = Field(default=10, gt=0)
    max_bytes: int = Field(default=8000, ge=512, le=32768)
    context_assembly: ContextAssembly | None = None
    capture_events: bool = False
    capture_checkpoint_every: int = Field(default=5, ge=1, le=100)
    capture_max_bytes: int = Field(default=8192, ge=512, le=32768)
    capture_log: Path | None = None
    trust_transport_security: bool = False

    @field_validator("context_assembly", mode="before")
    @classmethod
    def validate_assembly_support(cls, value: object) -> object:
        if value is not None and ContextAssembly is type(None):
            raise ValueError(  # noqa: TRY003
                "context_assembly requires a PowerContext core with text assembly support; "
                "install the core and adapter from the same checkout"
            )
        return value


def open_client(
    base_url: str,
    *,
    timeout: float,
    api_token: SecretStr | None = None,
    trust_transport_security: bool = False,
    allow_insecure_http: bool = False,
) -> AbstractAsyncContextManager[PowerContextClient]:
    """Open a client, vouching for the transport only when the operator opted in."""

    token = None if api_token is None else api_token.get_secret_value()
    if trust_transport_security:
        return _vouched_client(base_url, timeout, token=token, allow_insecure_http=allow_insecure_http)
    return PowerContextClient(base_url, token=token, timeout=timeout, allow_insecure_http=allow_insecure_http)


@asynccontextmanager
async def _vouched_client(
    base_url: str, timeout_seconds: float, *, token: str | None, allow_insecure_http: bool
) -> AsyncIterator[PowerContextClient]:
    # The operator vouched for the network (e.g. a private Compose bridge), and the
    # client only honours that vouch for a caller-supplied transport.
    async with (
        httpx.AsyncClient(timeout=timeout_seconds) as transport,
        PowerContextClient(
            base_url,
            token=token,
            http_client=transport,
            trust_transport_security=True,
            allow_insecure_http=allow_insecure_http,
        ) as client,
    ):
        yield client


class PowerContextPlugin:
    """Bub hooks backed by the public PowerContext client."""

    def __init__(self, framework: Any) -> None:
        self.settings = ensure_config(PowerContextSettings)
        self.base_url, self.allow_insecure_http = self.settings.resolve_transport()
        self._framework = framework
        self._scope_lock = asyncio.Lock()
        self._capture_lock = asyncio.Lock()

    @hookimpl
    def load_state(self, message: Any, session_id: str) -> TurnState:
        del message, session_id
        workspace_key = workspace_binding_key(getattr(self._framework, "workspace", None))
        binding_keys = [workspace_key] if workspace_key is not None else []
        return {
            STATE_KEY: {
                "base_url": self.base_url,
                "scope_id": None,
                "explicit_scope_id": self.settings.scope_id,
                "binding_keys": binding_keys,
                "timeout": self.settings.timeout,
                "api_token": self.settings.api_token,
                "trust_transport_security": self.settings.trust_transport_security,
                "allow_insecure_http": self.allow_insecure_http,
                "max_bytes": self.settings.max_bytes,
                "context_assembly": self.settings.context_assembly,
                "capture_sequence": 0,
                "captured_events": 0,
                "captured_position": 0,
                "flushed_position": 0,
                "prompt_captured": False,
                "recall_texts": {},
            }
        }

    @hookimpl(tryfirst=True)
    async def build_prompt(self, message: Any, session_id: str, state: TurnState) -> list[UserContent] | None:
        # Bub frames each inbound message with channel metadata and the time, which would outweigh the user's words in
        # lexical recall. Building the prompt here, ahead of the other implementations, ties every model-facing user
        # message, steered follow-ups included, to the text the user sent.
        if _BUILDING_PROMPT.get():
            # Another plugin delegating the same way called back in; let it continue with the rest.
            return None
        token = _BUILDING_PROMPT.set(True)
        try:
            built = await self._build_prompt_without_self(message, session_id, state)
        finally:
            _BUILDING_PROMPT.reset(token)
        prompt = to_content(built or content_of(message))
        # ``text`` is what the sender wrote when ``content`` wraps it with channel metadata (Bub after 0.6.1); only an
        # absent or None ``text`` means ``content`` is that text. An empty ``text`` recalls nothing rather than the
        # replied-to message and metadata in the envelope.
        text = field_of(message, "text")
        recall_text = field_of(message, "content") if text is None else text
        capture_state = state.get(STATE_KEY)
        if isinstance(capture_state, dict) and isinstance(recall_text, str):
            # A steered message reaches the model with its text parts joined as built; the turn's message passes
            # through Bub's legacy prompt form, which joins them with newlines.
            for key in {republic.user(*prompt).text.strip(), prompt_text(prompt).strip()}:
                capture_state["recall_texts"][key] = recall_text.strip()
        return prompt

    async def _build_prompt_without_self(self, message: Any, session_id: str, state: TurnState) -> Any:
        # Resolve like Bub's own call: in precedence order, awaiting each implementation until one returns a result.
        # Calling the subset caller instead would apply pluggy's synchronous first-result rule to coroutines.
        others = self._framework.plugin_manager.subset_hook_caller("build_prompt", remove_plugins=[self])
        kwargs = {"message": message, "session_id": session_id, "state": state}
        for impl in reversed(others.get_hookimpls()):
            result = impl.function(**{name: kwargs[name] for name in impl.argnames if name in kwargs})
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                return result
        return None

    @hookimpl
    def system_prompt(self, prompt: str | list[dict[str, Any]], state: TurnState) -> str:
        del prompt, state
        return GUIDANCE

    @hookimpl
    async def before_llm_call(self, request: LLMCallRequest, state: TurnState) -> LLMCallRequest | None:
        prompt = _latest_user_text(request.messages)
        if not prompt:
            return None

        scope_id = await self._scope_id(state)
        if scope_id is None:
            return None

        capture_state = state[STATE_KEY]
        if self.settings.capture_events and not capture_state["prompt_captured"]:
            capture_state["prompt_captured"] = True
            await self._capture_event(
                event="user_prompt",
                run_id=request.run_id,
                payload={"text": prompt},
                state=state,
            )

        if any(_contains_context_marker(message) for message in request.messages):
            return None

        # A prompt Bub did not build from an inbound message, such as a subagent's, is recalled as the model sees it.
        query = capture_state["recall_texts"].get(prompt, prompt)
        if not query:
            return None

        prepared_content = await self._prepare_context(query, scope_id, state)
        if not prepared_content:
            return None

        context_message = republic.system(f"{CONTEXT_MARKER}\n\n{prepared_content}")
        return replace(request, messages=[context_message, *request.messages])

    @hookimpl
    async def after_llm_call(self, request: LLMCallRequest, result: LLMCallResult, state: TurnState) -> None:
        if not self.settings.capture_events:
            return
        await self._capture_event(
            event="llm_result",
            run_id=request.run_id,
            payload={
                "text": result.text,
                "tool_calls": result.tool_calls,
                "error": _error_name(result.error),
                "duration_ms": result.duration_ms,
            },
            state=state,
        )

    @hookimpl
    async def after_tool_call(self, call: ToolCall, result: ToolCallResult, state: TurnState) -> None:
        if not self.settings.capture_events:
            return
        await self._capture_event(
            event="tool_result",
            run_id=call.run_id,
            payload={
                "tool": call.tool,
                "arguments": call.arguments,
                "result": result.result,
                "error": _error_name(result.error),
                "duration_ms": result.duration_ms,
            },
            state=state,
        )

    @hookimpl
    async def save_state(self, session_id: str, state: TurnState, message: Any, model_output: str) -> None:
        del session_id, message, model_output
        if not self.settings.capture_events:
            return
        async with self._capture_lock:
            await self._flush_captured_sources(state, final=True)

    def _client(self) -> AbstractAsyncContextManager[PowerContextClient]:
        return open_client(
            self.base_url,
            timeout=self.settings.timeout,
            api_token=self.settings.api_token,
            trust_transport_security=self.settings.trust_transport_security,
            allow_insecure_http=self.allow_insecure_http,
        )

    async def _scope_id(self, state: TurnState) -> str | None:
        scope_state = state[STATE_KEY]
        explicit_scope_id = scope_state["explicit_scope_id"]
        binding_keys = scope_state["binding_keys"]
        cached_scope_id = scope_state.get("scope_id")
        if cached_scope_id is not None:
            return cached_scope_id

        async with self._scope_lock:
            cached_scope_id = scope_state.get("scope_id")
            if cached_scope_id is not None:
                return cached_scope_id
            try:
                async with self._client() as client:
                    resolved_scope_id = await resolve_scope_id(
                        client,
                        explicit_scope_id=explicit_scope_id,
                        binding_keys=binding_keys,
                    )
            except CLIENT_ERRORS as exc:
                scope_state["scope_error"] = type(exc).__name__
                self._write_capture_record(
                    event="scope",
                    status="failed",
                    error=type(exc).__name__,
                )
                return None

            scope_state["scope_id"] = resolved_scope_id
            scope_state.pop("scope_error", None)
            return resolved_scope_id

    async def _prepare_context(self, query: str, scope_id: str, state: TurnState) -> str | None:
        request = PrepareContextRequest(
            scope_id=scope_id,
            query=_bounded_recall_query(query),
            max_bytes=self.settings.max_bytes,
            **({"assembly": self.settings.context_assembly} if self.settings.context_assembly is not None else {}),
        )
        try:
            async with self._client() as client:
                prepared = cast(PreparedContext, await client.prepare_context(request))
        except CLIENT_ERRORS as exc:
            state[STATE_KEY]["prepare_error"] = type(exc).__name__
            self._write_capture_record(
                event="context",
                status="failed",
                error=type(exc).__name__,
                captured_events=state[STATE_KEY]["captured_events"],
            )
            return None
        self._write_capture_record(
            event="context",
            status=prepared.status.value,
            content_bytes=prepared.content_bytes,
            captured_events=state[STATE_KEY]["captured_events"],
            flushed_position=state[STATE_KEY]["flushed_position"],
        )
        return prepared.content

    async def _capture_event(
        self,
        *,
        event: str,
        run_id: str,
        payload: dict[str, Any],
        state: TurnState,
    ) -> None:
        scope_id = await self._scope_id(state)
        if scope_id is None:
            return

        async with self._capture_lock:
            capture_state = state[STATE_KEY]
            capture_state["capture_sequence"] += 1
            sequence = capture_state["capture_sequence"]
            session_id = str(state.get("session_id", "unknown"))
            source_id = _source_id(scope_id, session_id, sequence, event, run_id)
            content = render_capture_event(event, sequence, payload, self.settings.capture_max_bytes)
            request = CaptureContentSourceRequest(
                scope_id=scope_id,
                source_id=source_id,
                content=content,
                metadata={
                    "origin": "bub",
                    "kind": "agent-trajectory",
                    "event": event,
                    "sequence": sequence,
                    "session_id": session_id,
                    "run_id": run_id,
                },
            )
            try:
                async with self._client() as client:
                    response = await client.capture_content_source(request)
            except CLIENT_ERRORS as exc:
                self._write_capture_record(
                    event=event,
                    sequence=sequence,
                    status="failed",
                    source_id=source_id,
                    error=type(exc).__name__,
                )
                return

            capture_state["captured_events"] += 1
            capture_state["captured_position"] = max(capture_state["captured_position"], response.position)
            self._write_capture_record(
                event=event,
                sequence=sequence,
                status="captured",
                source_id=source_id,
                source_position=response.position,
            )
            if capture_state["captured_events"] % self.settings.capture_checkpoint_every == 0:
                await self._flush_captured_sources(state, final=False)

    async def _flush_captured_sources(self, state: TurnState, *, final: bool) -> None:
        capture_state = state[STATE_KEY]
        target_position = capture_state["captured_position"]
        if target_position <= capture_state["flushed_position"]:
            return

        scope_id = await self._scope_id(state)
        if scope_id is None:
            return

        try:
            async with self._client() as client:
                response = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
        except CLIENT_ERRORS as exc:
            self._write_capture_record(
                event="checkpoint",
                status="failed",
                final=final,
                target_position=target_position,
                error=type(exc).__name__,
            )
            return

        capture_state["flushed_position"] = response.current_cursor
        self._write_capture_record(
            event="checkpoint",
            status=response.status.value,
            final=final,
            target_position=target_position,
            previous_cursor=response.previous_cursor,
            current_cursor=response.current_cursor,
            high_watermark=response.high_watermark,
            processed_source_count=response.processed_source_count,
            cursor_advanced=response.current_cursor > response.previous_cursor,
            remaining_work=response.remaining_work,
        )

    def _write_capture_record(self, *, event: str, status: str, **values: Any) -> None:
        if self.settings.capture_log is None:
            return
        record = {
            "schema": CAPTURE_SCHEMA,
            "recorded_at": datetime.now(UTC).isoformat(),
            "event": event,
            "status": status,
            **values,
        }
        self.settings.capture_log.parent.mkdir(parents=True, exist_ok=True)
        with self.settings.capture_log.open("a", encoding="utf-8") as capture_log:
            capture_log.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def _latest_user_text(messages: list[republic.Message]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.text.strip()
    return ""


def _bounded_recall_query(text: str) -> str:
    """Keep the most recent text of a long prompt, cut between characters, within both query bounds."""

    query = text.strip()
    if len(query) <= MAX_QUERY_CHARACTERS and len(query.encode("utf-8")) <= MAX_QUERY_BYTES:
        return query
    retained = query[-MAX_QUERY_CHARACTERS:]
    encoded = retained.encode("utf-8")
    if len(encoded) > MAX_QUERY_BYTES:
        retained = encoded[-MAX_QUERY_BYTES:].decode("utf-8", errors="ignore")
    return retained.strip()


def _contains_context_marker(message: republic.Message) -> bool:
    return CONTEXT_MARKER in message.text


def _source_id(scope_id: str, session_id: str, sequence: int, event: str, run_id: str) -> str:
    identity = "\0".join((scope_id, session_id, str(sequence), event, run_id))
    return f"bub-event:{hashlib.sha256(identity.encode()).hexdigest()}"


def _error_name(error: Exception | None) -> str | None:
    return None if error is None else type(error).__name__
