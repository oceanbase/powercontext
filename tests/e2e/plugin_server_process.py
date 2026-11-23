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

"""Own one TCP listener while the installed CLI initializes the real Server."""

from __future__ import annotations

import json
import os
import runpy
import socket
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch


def main() -> None:
    entrypoint, port_filename, *arguments = sys.argv[1:]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        _host, port = listener.getsockname()
        port_file = Path(port_filename)
        pending = port_file.with_suffix(".tmp")
        pending.write_text(json.dumps({"port": port, "pid": os.getpid()}))
        pending.replace(port_file)

        import uvicorn

        from powercontext.server import cli

        def run_bound_server(application: Any, *, host: str, port: int) -> None:
            assert listener.getsockname() == (host, port)
            server = uvicorn.Server(
                uvicorn.Config(application, host=host, port=port, access_log=False, log_config=None)
            )
            server.run(sockets=[listener])
            if not server.started:
                raise SystemExit(3)

        # Only this fixture owns the TCP leaf; CLI parsing, configuration and
        # Server startup/shutdown still run through the installed entry point.
        sys.argv = [entrypoint, *arguments, "--port", str(port)]
        with patch.object(cli, "_run_server", run_bound_server):
            runpy.run_path(entrypoint, run_name="__main__")


if __name__ == "__main__":
    main()
