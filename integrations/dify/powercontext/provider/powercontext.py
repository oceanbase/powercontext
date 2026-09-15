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
