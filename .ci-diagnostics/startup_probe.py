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

"""Private, opt-in Uvicorn startup observations for the CI diagnostic cohort."""

from contextvars import ContextVar
from functools import wraps
import json
import logging
import os
from pathlib import Path
import sys
from threading import Lock
import time
import traceback

_ACTIVE = ContextVar("ci_server_startup", default=None)
_LOCK = Lock()
_INSTALLED = False


def _emit(server, context, event, **details):
    # Observation failures must not replace a startup result or exception.
    try:
        now = time.monotonic()
        lifespan = getattr(server, "lifespan", None)
        record = {
            **context,
            "event": event,
            "monotonic": now,
            "elapsed_seconds": now - context["start_monotonic"],
            "started": getattr(server, "started", None),
            "should_exit": getattr(server, "should_exit", None),
            "lifespan_should_exit": getattr(lifespan, "should_exit", None),
            "lifespan_startup_failed": getattr(lifespan, "startup_failed", None),
            "lifespan_error_occurred": getattr(lifespan, "error_occurred", None),
            **details,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        directory = Path(os.environ["PC_CI_DIAGNOSTIC_DIR"])
        with _LOCK:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"server-startup-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
        if event == "uvicorn_error":
            sys.stderr.write("[ci-server-startup] " + line)
            sys.stderr.flush()
    except BaseException:
        pass


def install():
    global _INSTALLED
    if _INSTALLED or os.environ.get("PC_CI_TRACE_SERVER_STARTUP") != "1":
        return
    import uvicorn

    original_startup = uvicorn.Server.startup
    logger = logging.getLogger("uvicorn.error")
    original_error = logger.error

    @wraps(original_error)
    def error(message, *args, **kwargs):
        active = _ACTIVE.get()
        if active is not None:
            server, context = active
            try:
                text = str(message) % args if args else str(message)
                exception = kwargs.get("exc_info")
                if isinstance(exception, BaseException):
                    exception = (type(exception), exception, exception.__traceback__)
                elif exception and not isinstance(exception, tuple):
                    exception = sys.exc_info()
                # Format existing error information only, without frame locals,
                # application configuration, request data or environment dumps.
                detail = "".join(traceback.format_exception(*exception)) if exception else None
                _emit(server, context, "uvicorn_error", logger="uvicorn.error", message=text, traceback=detail)
            except BaseException:
                pass
        return original_error(message, *args, **{**kwargs, "stacklevel": kwargs.get("stacklevel", 1) + 1})

    @wraps(original_startup)
    async def startup(server, *args, **kwargs):
        sockets = kwargs.get("sockets", args[0] if args else None)
        addresses = []
        for listener in sockets or ():
            try:
                address = listener.getsockname()
                if isinstance(address, tuple) and len(address) >= 2:
                    addresses.append({"host": address[0], "port": address[1]})
            except OSError:
                pass
        context = {
            "pid": os.getpid(),
            "server_id": id(server),
            "pytest_current_test": os.environ.get("PYTEST_CURRENT_TEST"),
            "host": server.config.host,
            "port": server.config.port,
            "provided_socket_addresses": addresses,
            "start_monotonic": time.monotonic(),
        }
        token = _ACTIVE.set((server, context))
        _emit(server, context, "startup_begin")
        try:
            result = await original_startup(server, *args, **kwargs)
        except BaseException as exception:
            details = {"exception_type": type(exception).__name__}
            if isinstance(exception, SystemExit) and isinstance(exception.code, (int, type(None))):
                details["exit_code"] = exception.code
            if isinstance(exception, OSError):
                details["errno"] = exception.errno
            _emit(server, context, "startup_exception", **details)
            raise
        else:
            _emit(server, context, "startup_return")
            return result
        finally:
            _emit(server, context, "startup_end")
            _ACTIVE.reset(token)

    logger.error = error
    uvicorn.Server.startup = startup
    _INSTALLED = True
