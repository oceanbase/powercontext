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

"""Authoritative static discovery contract for the PowerContext Server."""

from __future__ import annotations

from importlib.metadata import version

from packaging.version import Version

from powercontext.http import ServerInfo
from powercontext.http._generated.operations import API_VERSION

_SCHEMA_VERSION = {"major": 1, "minor": 0}
_FEATURE_CONTRACTS = {
    "access.principal": {
        "version": {"major": 1, "minor": 0},
        "operations": ["get_access_principal"],
    },
    "scope.selection": {
        "version": {"major": 1, "minor": 0},
        "operations": ["list_scopes", "get_scope", "get_default_scope"],
    },
    "memory.explicit": {
        "version": {"major": 1, "minor": 0},
        "operations": ["remember_memory", "search_memory", "get_memory_entry"],
    },
}


def server_info(server_id: str) -> ServerInfo:
    """Build discovery metadata from the generated API contract and installed package."""

    api_version = Version(API_VERSION)
    return ServerInfo.model_validate({
        "schema_version": _SCHEMA_VERSION,
        "product": "powercontext",
        "server_id": server_id,
        "package_version": version("powercontext"),
        "api_contract_version": {"major": api_version.major, "minor": api_version.minor},
        "feature_contracts": _FEATURE_CONTRACTS,
    })


__all__ = ("server_info",)
