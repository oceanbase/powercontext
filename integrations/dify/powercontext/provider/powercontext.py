"""Validate a PC connection using its read-only capabilities endpoint."""

import asyncio
from typing import Any

from bridge import Connection
from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from powercontext.client import PowerContextClient


class PowerContextProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            connection = Connection.model_validate(credentials)
            asyncio.run(self._check(connection))
        except Exception:
            message = "PowerContext connection could not be validated."
            raise ToolProviderCredentialValidationError(message) from None

    async def _check(self, connection: Connection) -> None:
        async with PowerContextClient(
            connection.base_url,
            token=connection.token.get_secret_value(),
            timeout=10,
            allow_insecure_http=connection.allow_insecure_http,
        ) as client:
            await client.get_capabilities()
