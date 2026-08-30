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

"""Shared network transport safety policy for PowerContext surfaces.

Every surface that opens or configures an HTTP connection to a Server -- the
Python Client, the CLI, and the Agent integrations -- follows the same rule:
plaintext HTTP is only trusted on a loopback address. Bearer credentials must
never leave the machine over an unencrypted connection, and an unauthenticated
Server must not bind to a routable address without an explicit opt-in.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

#: Named hosts for which unencrypted HTTP is always considered safe. IP literals
#: are additionally accepted whenever they fall inside a loopback range -- for
#: IPv4 that is the whole ``127.0.0.0/8`` block, not just ``127.0.0.1``.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_host(host: str | None) -> bool:
    """Return whether ``host`` names a loopback interface.

    Accepts bare hostnames (``localhost``), IPv4 literals anywhere in the
    ``127.0.0.0/8`` block (``127.0.0.1``, ``127.0.0.2``, ...), and IPv6 loopback
    literals with or without brackets (``::1`` / ``[::1]``).
    """

    if not host:
        return False
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_plaintext_non_loopback(url: str) -> bool:
    """Return whether ``url`` sends plaintext HTTP to a non-loopback host."""

    parsed = urlsplit(url)
    return parsed.scheme == "http" and not is_loopback_host(parsed.hostname)


__all__ = [
    "LOOPBACK_HOSTS",
    "is_loopback_host",
    "is_plaintext_non_loopback",
]
