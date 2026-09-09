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

"""Pinned, mandatory TLS for the process-owned native evaluation profile."""

from __future__ import annotations

# ruff: noqa: TRY003 - bounded transport diagnostics never include connection values.
import hashlib
import importlib
import importlib.metadata
import re
import ssl
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from powercontext_datus.freeze import IntegrityError


def trust_bytes(policy: dict[str, Any], material: str | None = None) -> str:
    if not isinstance(policy, dict) or set(policy) != {"ca_file", "ca_sha256"}:
        raise IntegrityError("an exact CA file and digest are required")
    if not isinstance(policy["ca_file"], str) or not Path(policy["ca_file"]).is_absolute():
        raise IntegrityError("CA reference must be absolute")
    if not isinstance(policy["ca_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", policy["ca_sha256"]):
        raise IntegrityError("invalid CA digest")
    if material is None:
        path = Path(policy["ca_file"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise IntegrityError("CA reference must be a bounded regular file")
        data = path.read_bytes()
    else:
        data = material.encode("ascii")
    if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != policy["ca_sha256"]:
        raise IntegrityError("approved CA bytes changed")
    return data.decode("ascii")


def tls_context(policy: dict[str, Any], material: str | None = None) -> ssl.SSLContext:
    # No system roots or environment-selected CA files are implicitly trusted.
    pem = trust_bytes(policy, material)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.hostname_checks_common_name = False
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=pem)
    return context


def mysql_connection(database: dict[str, Any], password: str, context: ssl.SSLContext) -> Any:
    """Every physical connection uses REQUIRED TLS on its authentication socket."""
    if importlib.metadata.version("pymysql") != "1.2.0":
        raise IntegrityError("unverified MySQL TLS driver version")
    if (
        context.verify_mode != ssl.CERT_REQUIRED
        or not context.check_hostname
        or context.minimum_version < ssl.TLSVersion.TLSv1_2
    ):
        raise IntegrityError("database TLS verification cannot be disabled")
    cls = importlib.import_module("pymysql.connections").Connection
    connection = cls(
        host=database["host"],
        port=database["port"],
        user=database["username"],
        password=password,
        database=database["name"],
        charset="utf8mb4",
        ssl=context,
        defer_connect=True,
        local_infile=False,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )
    # Assert the locked driver's explicit-SSL REQUIRED branch, not PREFERRED.
    if not connection.ssl or not connection._ssl_required or connection.ctx is not context:
        connection._force_close()
        raise IntegrityError("database driver cannot enforce REQUIRED TLS")
    try:
        connection.connect()
    except BaseException:
        connection._force_close()
        raise
    return connection


def mysql_connector(database: dict[str, Any], password: str, material: str | None = None) -> Any:
    context = tls_context(database["tls"], material)
    cls = importlib.import_module("datus_mysql").MySQLConnector
    sqlalchemy = importlib.import_module("sqlalchemy")
    frozen = dict(database)

    class RequiredTLSConnector(cls):
        _secured_engine = None

        def _ensure_engine(self):
            with self._engine_lock:
                if self.engine is not None:
                    if self.engine is not self._secured_engine or not self._owns_engine:
                        raise IntegrityError("foreign database engine is not admitted")
                    return self.engine

                def connect():
                    return mysql_connection(frozen, password, context)

                self.engine = sqlalchemy.create_engine(
                    "mysql+pymysql://",
                    creator=connect,
                    pool_size=10,
                    max_overflow=20,
                    pool_timeout=self.timeout_seconds,
                    pool_recycle=3600,
                    pool_pre_ping=True,
                )
                self._secured_engine = self.engine
                self._owns_engine = True
                return self.engine

    # Never place the password in the native connector's URL/config/repr.
    connector = RequiredTLSConnector({
        "host": frozen["host"],
        "port": frozen["port"],
        "username": frozen["username"],
        "database": frozen["name"],
        "password": "",
    })
    connector.connection_string = "mysql+pymysql://"
    return connector


@contextmanager
def model_tls(context: ssl.SSLContext):
    """Worker-process ownership only; restore even when native initialization fails."""
    httpx = importlib.import_module("httpx")
    originals = []

    def send(original):
        def checked(self, request, *args, **kwargs):
            kwargs["follow_redirects"] = False
            return original(self, request, *args, **kwargs)

        return checked

    def secured(original):
        def initialize(self, *args, **kwargs):
            if (
                kwargs.get("transport") is not None
                or kwargs.get("mounts")
                or kwargs.get("proxy")
                or kwargs.get("proxies")
            ):
                raise IntegrityError("unapproved model transport override")
            kwargs.update(verify=context, trust_env=False, follow_redirects=False)
            original(self, *args, **kwargs)

        return initialize

    try:
        for cls in (httpx.Client, httpx.AsyncClient):
            for name, wrapper in (("__init__", secured), ("send", send)):
                original = getattr(cls, name)
                originals.append((cls, name, original))
                setattr(cls, name, wrapper(original))
        yield
    finally:
        for cls, name, original in reversed(originals):
            setattr(cls, name, original)
