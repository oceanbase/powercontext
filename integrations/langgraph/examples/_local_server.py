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

"""Start a throwaway PowerContext Server so the examples run without external setup.

Production deployments point the adapter at a separately managed Server through
``POWERCONTEXT_LANGGRAPH_BASE_URL`` and run ``powercontext server run``. The examples start their own
short-lived Server on an ephemeral port instead, so a reader can run them with nothing but this package
installed. The Server uses an on-disk SQLite database in a temporary directory and the built-in ``test``
generation model, and is torn down when the block exits.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import TemporaryDirectory

import uvicorn
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


@contextmanager
def local_powercontext_server(*, token: str | None = None) -> Iterator[str]:
    """Run a local PowerContext Server for the duration of the block and yield its base URL.

    When ``token`` is set the Server requires bearer authentication with that token.
    """

    with TemporaryDirectory() as db_dir:
        settings = ServerSettings(
            auth=BearerAuthConfig(enabled=token is not None, token=SecretStr(token or "")),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{db_dir}/memory.db"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        )
        app = create_server_app(settings=settings)

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        host, port = listener.getsockname()
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="on"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()

        deadline = time.monotonic() + 10
        while thread.is_alive() and not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("local PowerContext Server did not start")  # noqa: TRY003

        try:
            yield f"http://{host}:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
