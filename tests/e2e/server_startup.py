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

"""Wait for real Server setup independently of business-operation deadlines."""

import threading
import time

import uvicorn

SERVER_STARTUP_TIMEOUT_SECONDS = 60.0


def wait_for_server_startup(server: uvicorn.Server, thread: threading.Thread) -> None:
    deadline = time.monotonic() + SERVER_STARTUP_TIMEOUT_SECONDS
    while thread.is_alive() and not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        if not thread.is_alive():
            raise RuntimeError("Server thread exited before startup completed")  # noqa: TRY003
        raise TimeoutError(f"Server did not start within {SERVER_STARTUP_TIMEOUT_SECONDS:g} seconds")  # noqa: TRY003
