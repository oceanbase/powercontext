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

"""Allowlisted HTTPS egress for one run network.

An evaluation run's containers sit on an internal Docker network, so their only way out is this proxy, bound to the
network's gateway on the host. It accepts ``CONNECT`` only, decides by host name and port, and never terminates TLS.
The allowlist changes with the run phase: dependency installation may reach package indexes, while the agent may
reach only its model endpoint, because a package index or source host can serve the reference fix.

The name in ``CONNECT`` only chooses the upstream address, and one address can serve many HTTPS sites. The proxy
therefore also reads the TLS ClientHello and requires its server name (SNI) to be the allowed host. It cannot see the
encrypted ``Host`` header; sending a different ``Host`` under a matching SNI is left to the provider's front end, which
for the supported model hosts rejects mismatched names.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# uv resolves and downloads packages and managed Python builds from these hosts during prewarm.
PREWARM_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org", "releases.astral.sh"})
# Model hosts per Codex login: API keys call the OpenAI API; ChatGPT logins call ChatGPT and refresh tokens.
API_KEY_MODEL_HOSTS = frozenset({"api.openai.com"})
CHATGPT_MODEL_HOSTS = frozenset({"chatgpt.com", "auth.openai.com"})
# uv downloads managed Python builds from GitHub unless pointed at this mirror; older uv versions default to GitHub.
PYTHON_INSTALL_MIRROR = "https://releases.astral.sh/github/python-build-standalone/releases/download"

_HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")
_MAX_HEADER_BYTES = 8 * 1024
_HEADER_SECONDS = 10.0
_CONNECT_SECONDS = 15.0
_MAX_TUNNELS = 256
_MAX_CLIENT_HELLO_BYTES = 16 * 1024


class EgressError(ValueError):
    """The egress policy or proxy cannot be used safely."""


def model_hosts(auth_json: Path) -> frozenset[str]:
    """The model hosts Codex needs for the login in ``auth.json``, read without exposing its credentials."""

    try:
        auth = json.loads(auth_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EgressError("Codex auth.json is unreadable") from error
    if not isinstance(auth, dict):
        raise EgressError("Codex auth.json is not an object")
    mode = auth.get("auth_mode")
    if mode == "chatgpt" or (mode is None and not auth.get("OPENAI_API_KEY") and auth.get("tokens")):
        return CHATGPT_MODEL_HOSTS
    return API_KEY_MODEL_HOSTS


def codex_provider_hosts(codex_config: Path | None) -> frozenset[str]:
    """The HTTPS host of the custom model provider selected in a Codex ``config.toml``, if any.

    A provider reached over plain HTTP (for example a gateway on the Docker host) bypasses the proxy through the
    no-proxy list instead, and IP literals are never added.
    """

    if codex_config is None:
        return frozenset()
    try:
        config = tomllib.loads(codex_config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EgressError("Codex config.toml is unreadable") from error
    provider = config.get("model_provider")
    providers = config.get("model_providers")
    if not isinstance(provider, str) or not isinstance(providers, dict):
        return frozenset()
    entry = providers.get(provider)
    base_url = entry.get("base_url") if isinstance(entry, dict) else None
    if not isinstance(base_url, str):
        return frozenset()
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or _HOST.fullmatch(host) is None:
        return frozenset()
    return frozenset({host})


def validated_hosts(hosts: Iterable[str]) -> frozenset[str]:
    """Lower-case DNS names only: an IP literal or wildcard would bypass name-based review."""

    values = frozenset(host.strip().lower() for host in hosts)
    if any(_HOST.fullmatch(host) is None for host in values):
        raise EgressError("Egress hosts must be DNS names")
    return values


@dataclass(frozen=True)
class EgressPolicy:
    """Hosts reachable during dependency installation and during the agent run."""

    prewarm: frozenset[str] = PREWARM_HOSTS
    agent: frozenset[str] = API_KEY_MODEL_HOSTS
    ports: frozenset[int] = field(default_factory=lambda: frozenset({443}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "prewarm", validated_hosts(self.prewarm))
        object.__setattr__(self, "agent", validated_hosts(self.agent))
        if not self.ports or any(not 0 < port < 65536 for port in self.ports):
            raise EgressError("Egress ports are invalid")


class AllowlistProxy:
    """One host-side CONNECT proxy whose allowlist the run switches between phases."""

    def __init__(self, *, ports: frozenset[int] = frozenset({443})) -> None:
        self._ports = ports
        self._allowed: frozenset[str] = frozenset()
        self._phase = "closed"
        self._decisions: Counter[tuple[str, str, bool]] = Counter()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None
        self._tunnels = threading.BoundedSemaphore(_MAX_TUNNELS)

    def start(self, address: str) -> str:
        """Listen on ``address`` (the run network's gateway) and return the proxy URL for containers."""

        loop = asyncio.new_event_loop()
        ready = threading.Event()
        failure: list[BaseException] = []

        async def listen() -> None:
            self._server = await asyncio.start_server(self._handle, host=address, port=0)

        def run() -> None:
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(listen())
            except BaseException as error:  # noqa: BLE001 - reported to the starting thread
                failure.append(error)
                ready.set()
                return
            ready.set()
            loop.run_forever()

        self._loop = loop
        self._thread = threading.Thread(target=run, name="egress-proxy", daemon=True)
        self._thread.start()
        if not ready.wait(timeout=5.0) or failure or self._server is None:
            self.stop()
            raise EgressError("Egress proxy failed to start") from (failure[0] if failure else None)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://{address}:{port}"

    def allow(self, phase: str, hosts: frozenset[str]) -> None:
        """Replace the allowlist; tunnels already open keep running, new ones follow the new list."""

        with self._lock:
            self._phase = phase
            self._allowed = hosts

    def decisions(self) -> list[dict[str, object]]:
        """Connection counts by phase, host and decision, for the run's evidence."""

        with self._lock:
            items = sorted(self._decisions.items())
        return [
            {"phase": phase, "host": host, "allowed": allowed, "connections": count}
            for (phase, host, allowed), count in items
        ]

    def stop(self) -> None:
        loop, server, thread = self._loop, self._server, self._thread
        self._loop = self._server = self._thread = None
        if loop is None:
            return

        async def close() -> None:
            if server is not None:
                server.close()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(server.wait_closed(), timeout=2)
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()

        if loop.is_running():
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(close(), loop).result(timeout=5)
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)
        if not loop.is_running():
            loop.close()

    def _permitted(self, host: str, port: int) -> tuple[str, bool]:
        with self._lock:
            return self._phase, port in self._ports and host in self._allowed

    def _record(self, phase: str, label: str, allowed: bool) -> None:
        with self._lock:
            self._decisions[(phase, label, allowed)] += 1

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self._tunnels.acquire(blocking=False):
            writer.close()
            return
        try:
            await self._serve(reader, writer)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            self._tunnels.release()
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_HEADER_SECONDS)
        if len(header) > _MAX_HEADER_BYTES:
            return await _reply(writer, b"431 Request Header Fields Too Large")
        method, _, rest = header.split(b"\r\n", 1)[0].decode("latin-1").partition(" ")
        if method != "CONNECT":
            return await _reply(writer, b"405 Method Not Allowed")
        target = rest.split(" ", 1)[0]
        host, _, port_text = target.rpartition(":")
        host = host.lower()
        phase, allowed = self._permitted(host, int(port_text)) if port_text.isdigit() else (self._phase, False)
        if not allowed:
            # Record names, not arbitrary request text: a refused target that is not a DNS name is grouped.
            self._record(phase, host if _HOST.fullmatch(host) else "<invalid>", False)
            return await _reply(writer, b"403 Forbidden")
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        hello = await asyncio.wait_for(_read_client_hello(reader), timeout=_HEADER_SECONDS)
        if hello is None or _server_name(hello) != host:
            self._record(phase, f"{host} (sni mismatch)", False)
            return None
        self._record(phase, host, True)
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port_text)), timeout=_CONNECT_SECONDS
        )
        try:
            upstream_writer.write(hello)
            await upstream_writer.drain()
            await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))
        finally:
            upstream_writer.close()
        return None


async def _read_client_hello(reader: asyncio.StreamReader) -> bytes | None:
    """The client's first TLS record, which must carry a complete ClientHello."""

    header = await reader.readexactly(5)
    length = int.from_bytes(header[3:5], "big")
    if header[0] != 0x16 or length > _MAX_CLIENT_HELLO_BYTES:
        return None
    return header + await reader.readexactly(length)


def _server_name(record: bytes) -> str | None:
    """The host name from the server_name extension of a ClientHello record, lower-cased."""

    try:
        body = memoryview(record)[5:]
        if body[0] != 0x01:
            return None
        position = 4 + 2 + 32  # handshake header, client version, random
        position += 1 + body[position]  # session id
        position += 2 + int.from_bytes(body[position : position + 2], "big")  # cipher suites
        position += 1 + body[position]  # compression methods
        end = position + 2 + int.from_bytes(body[position : position + 2], "big")
        position += 2
        while position + 4 <= end:
            kind = int.from_bytes(body[position : position + 2], "big")
            size = int.from_bytes(body[position + 2 : position + 4], "big")
            data = body[position + 4 : position + 4 + size]
            if kind == 0 and data[2] == 0:  # server_name list with one host_name entry
                name_length = int.from_bytes(data[3:5], "big")
                return bytes(data[5 : 5 + name_length]).decode("ascii").lower()
            position += 4 + size
    except (IndexError, UnicodeDecodeError):
        return None
    return None


async def _reply(writer: asyncio.StreamWriter, status: bytes) -> None:
    writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    await writer.drain()


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(64 * 1024):
            sink.write(chunk)
            await sink.drain()
    finally:
        with contextlib.suppress(OSError):
            if sink.can_write_eof():
                sink.write_eof()
