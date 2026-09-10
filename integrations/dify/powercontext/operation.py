"""Dify SDK adapter. Diagnostics never include credentials or captured content."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Generator
from typing import Any, ClassVar

from bridge import Connection, MemoryIdentity, execute
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from powercontext.client import PowerContextClient, ServerResponseError, TransportError
from pydantic import ValidationError

logger = logging.getLogger(__name__)


class MemoryOperation(Tool):
    operation: ClassVar[str]

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            connection = Connection.model_validate(self.runtime.credentials)
            identity_value = tool_parameters.get("memory_context")
            if not identity_value:
                identity_value = {"app_id": self.session.app_id, "subject_id": self.runtime.user_id}
            identity = MemoryIdentity.model_validate(_object(identity_value))
            request = _object(tool_parameters.get("request", {}))
            result = asyncio.run(self._execute(connection, identity, request))
        except Exception as exc:
            outcome = _outcome(exc)
            logger.warning(json.dumps({"component": "powercontext.dify", "event": self.operation, "outcome": outcome}))
            yield self.create_json_message({"status": "error", "error": outcome})
            return
        yield self.create_json_message(result)

    async def _execute(
        self, connection: Connection, identity: MemoryIdentity, request: dict[str, Any]
    ) -> dict[str, Any]:
        async with asyncio.timeout(10):
            async with PowerContextClient(
                connection.base_url,
                token=connection.token.get_secret_value(),
                timeout=10,
                allow_insecure_http=connection.allow_insecure_http,
            ) as client:
                return await execute(client, connection, identity, self.operation, request)


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")  # noqa: TRY003
    return value


def _outcome(exc: Exception) -> str:
    if isinstance(exc, TimeoutError | TransportError):
        return "server_unavailable"
    if isinstance(exc, ServerResponseError):
        return {
            401: "authentication_failed",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
            503: "server_unavailable",
        }.get(exc.status_code, "invalid_response")
    if isinstance(exc, TypeError | ValueError | ValidationError):
        return "invalid_request"
    return "invalid_response"
