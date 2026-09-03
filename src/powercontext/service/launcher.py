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

"""Manager-owned preflight launcher for the persistent personal Server."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Generator
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit

from powercontext.server import cli as server_cli
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
from powercontext.service.environment import ProtectedEnvironmentFileError, load_protected_environment_file
from powercontext.service.model import EnvironmentFileIdentity, ProbeState
from powercontext.service.probe import probe_server
from powercontext.transport import is_loopback_host

logger = logging.getLogger(__name__)


@contextmanager
def _redirect_output(stdout_path: Path | None, stderr_path: Path | None) -> Generator[None, None, None]:
    with ExitStack() as stack:
        stdout = stack.enter_context(_open_output(stdout_path)) if stdout_path is not None else sys.stdout
        stderr = stack.enter_context(_open_output(stderr_path)) if stderr_path is not None else sys.stderr
        with redirect_stdout(stdout), redirect_stderr(stderr):
            yield


@contextmanager
def _open_output(path: Path) -> Generator[TextIO, None, None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", buffering=1) as output:
        yield output


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="powercontext-personal-service-launcher")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--env-file-device", type=int)
    parser.add_argument("--env-file-inode", type=int)
    parser.add_argument("--env-file-size", type=int)
    parser.add_argument("--env-file-modified-ns", type=int)
    parser.add_argument("--env-file-owner-uid", type=int)
    parser.add_argument("--env-file-mode", type=int)
    parser.add_argument("--env-file-owner-sid")
    parser.add_argument("--stdout", type=Path)
    parser.add_argument("--stderr", type=Path)
    options = parser.parse_args(arguments)

    try:
        with _redirect_output(options.stdout, options.stderr):
            environment: dict[str, str] | None = None
            if options.env_file is not None:
                identity = _environment_identity(options)
                environment = load_protected_environment_file(options.env_file, expected=identity).values
            with server_settings_context(environment=environment, data_dir=options.data_dir) as settings:
                expected = _endpoint(settings.http.host, settings.http.port)
                if expected != options.endpoint or not is_loopback_host(settings.http.host):
                    logger.error(
                        "Registered personal service endpoint does not match the current loopback Server configuration"
                    )
                    return 1
                probe = probe_server(options.endpoint)
                if probe.state is ProbeState.LIVE:
                    logger.info("PowerContext Server is already live; the personal service launcher will exit")
                    return 0
                if probe.state is ProbeState.CONFLICT:
                    logger.error("Personal service endpoint conflict: %s", probe.detail)
                    return 1
                server_cli._run_configured_server(settings)
                return 0
    except (ProtectedEnvironmentFileError, ServerConfigurationError) as error:
        logger.error("Personal service configuration is invalid: %s", error)  # noqa: TRY400
        return 1
    except SystemExit as error:
        if error.code is None or error.code == 0:
            return 0
        logger.error("PowerContext personal service exited during startup: %s", error)  # noqa: TRY400
        return 1
    except Exception:
        logger.exception("PowerContext personal service failed")
        return 1


def _environment_identity(options: argparse.Namespace) -> EnvironmentFileIdentity:
    fields = {
        "device": options.env_file_device,
        "inode": options.env_file_inode,
        "size": options.env_file_size,
        "modified_ns": options.env_file_modified_ns,
        "owner_uid": options.env_file_owner_uid,
        "mode": options.env_file_mode,
        "owner_sid": options.env_file_owner_sid,
    }
    if any(value is None for name, value in fields.items() if name != "owner_sid"):
        raise ProtectedEnvironmentFileError("the installed --env-file identity is incomplete")  # noqa: TRY003
    return EnvironmentFileIdentity(path=str(options.env_file), **fields)


def _endpoint(host: str, port: int) -> str:
    normalized = host.strip("[]")
    rendered_host = f"[{normalized}]" if ":" in normalized else normalized
    endpoint = f"http://{rendered_host}:{port}"
    parsed = urlsplit(endpoint)
    if parsed.hostname is None:
        raise ValueError("invalid Server endpoint")  # noqa: TRY003
    return endpoint


if __name__ == "__main__":
    raise SystemExit(main())
