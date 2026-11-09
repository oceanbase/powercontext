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

"""A text-only Pydantic AI model backed by genuine Claude Code print mode."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from contextlib import suppress
from typing import Any

import anyio
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryFeedbackPart,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from typing_extensions import override


class ClaudeCliModel(Model):
    """Run one fresh text turn per model request, with no caller tools or sessions.

    Authentication and all internal provider retries remain owned by Claude Code.
    This is deliberately narrower than Magpie's persistent MCP/session bridge.
    """

    def __init__(self, model_name: str, *, executable: str | None = None) -> None:
        super().__init__()
        self._model_name = model_name
        self._executable = executable or os.getenv("CLAUDE_CLI_EXECUTABLE") or "claude"

    @property
    @override
    def model_name(self) -> str:
        return self._model_name

    @property
    @override
    def system(self) -> str:
        return "claude-cli"

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        settings, parameters = self.prepare_request(model_settings, model_request_parameters)
        if (
            (settings and set(settings) - {"max_tokens"})
            or parameters.function_tools
            or parameters.output_tools
            or parameters.native_tools
        ):
            message = "Claude CLI supports text and prompted JSON only, without model settings or tools"
            raise UserError(message)
        if any(
            os.getenv(name)
            for name in (
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_USE_FOUNDRY",
                "CLAUDE_CODE_OAUTH_TOKEN",
            )
        ):
            message = "Claude CLI subscription requires its existing login without API or cloud credential overrides"
            raise UserError(message)
        max_tokens = None if settings is None else settings.get("max_tokens")
        if max_tokens is not None and (
            not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0
        ):
            message = "Claude CLI max_tokens must be a positive integer"
            raise UserError(message)
        prompt = self._prompt(self.prepare_messages(messages, parameters))
        with tempfile.TemporaryDirectory(prefix="powercontext-claude-") as directory:
            status = await self._run(["auth", "status"], directory=directory)
            if status.get("authMethod") != "claude.ai":
                message = "Claude CLI requires a Claude account login; run claude auth login"
                raise UserError(message)
            result = await self._run(
                [
                    "--print",
                    "--output-format",
                    "json",
                    "--model",
                    self._model_name,
                    "--tools",
                    "",
                    "--disallowedTools",
                    "mcp__*",
                    "--strict-mcp-config",
                    "--mcp-config",
                    '{"mcpServers":{}}',
                    "--setting-sources",
                    "",
                    "--safe-mode",
                    "--no-session-persistence",
                    "--max-turns",
                    "1",
                ],
                directory=directory,
                prompt=prompt,
                max_tokens=max_tokens,
            )
        if result.get("is_error") or not isinstance(result.get("result"), str):
            message = "Claude CLI did not complete a text response"
            raise UserError(message)
        usage = result.get("usage") or {}
        return ModelResponse(
            parts=[TextPart(result["result"])],
            model_name=self.model_name,
            provider_name=self.system,
            usage=RequestUsage(
                input_tokens=sum(
                    usage.get(key, 0)
                    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                ),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
            ),
            finish_reason="stop",
        )

    @staticmethod
    def _prompt(messages: list[ModelMessage]) -> bytes:
        transcript = []
        for message in messages:
            instructions = getattr(message, "instructions", None)
            if instructions:
                transcript.append({"role": "system", "content": instructions})
            for part in message.parts:
                if isinstance(part, (SystemPromptPart, TextPart)):
                    role = "system" if isinstance(part, SystemPromptPart) else "assistant"
                    transcript.append({"role": role, "content": part.content})
                elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    transcript.append({"role": "user", "content": part.content})
                elif isinstance(part, RetryFeedbackPart):
                    transcript.append({"role": "user", "content": part.model_response()})
                else:
                    error = "Claude CLI integration accepts text-only message history"
                    raise UserError(error)
        return json.dumps(transcript, ensure_ascii=False).encode()

    async def _run(
        self, arguments: list[str], *, directory: str, prompt: bytes | None = None, max_tokens: int | None = None
    ) -> dict[str, Any]:
        try:
            process = await asyncio.create_subprocess_exec(
                self._executable,
                *arguments,
                cwd=directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=os.name == "posix",
                env=None if max_tokens is None else {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_tokens)},
            )
        except OSError:
            message = "Cannot start Claude CLI; install it or set CLAUDE_CLI_EXECUTABLE"
            raise UserError(message) from None
        exchange = asyncio.create_task(process.communicate(prompt))
        try:
            async with asyncio.timeout(300):
                output, _ = await asyncio.shield(exchange)
        except BaseException:
            with anyio.CancelScope(shield=True):
                # Reap the real child and, on POSIX, descendants holding its pipes.
                with suppress(ProcessLookupError):
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                try:
                    async with asyncio.timeout(5):
                        await asyncio.shield(exchange)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    await exchange
            raise
        try:
            result = json.loads(output)
            if process.returncode or not isinstance(result, dict):
                raise ValueError  # noqa: TRY301 - suppress subprocess output in error reports
        except (ValueError, TypeError):
            message = "Claude CLI failed or returned malformed JSON"
            raise UserError(message) from None
        return result
