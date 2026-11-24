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

"""Match scoped HTTP operations without splitting encoded resource identifiers."""

from urllib.parse import quote, unquote

from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import Scope
from typing_extensions import override


def encoded_routing_scope(scope: Scope) -> Scope:
    raw_path = scope.get("raw_path")
    if scope["type"] != "http" or not isinstance(raw_path, bytes):
        return scope
    try:
        path = raw_path.decode("ascii")
    except UnicodeDecodeError:
        return scope
    return {**scope, "path": path, "root_path": quote(scope.get("root_path", ""), safe="/")}


class EncodedPathAPIRoute(APIRoute):
    """Match scoped resources against raw paths and decode each identity exactly once."""

    @override
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        encoded = encoded_routing_scope(scope)
        match, child_scope = super().matches(encoded)
        if encoded is not scope and match is not Match.NONE:
            child_scope["path_params"] = {
                key: unquote(value) if isinstance(value, str) else value
                for key, value in child_scope.get("path_params", {}).items()
            }
        return match, child_scope
