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

"""Operation identity and mutation semantics shared by SDKs and host adapters."""

from powercontext.http._generated import operations

OPERATIONS = {
    value.operation_id: value for value in vars(operations).values() if isinstance(value, operations.Operation)
}


def is_write(operation_id: str) -> bool:
    operation = OPERATIONS[operation_id]
    if operation.method == "GET":
        return False
    if operation.access is not None and operation.access.action in {"scope.read", "server.observe"}:
        return False
    return not operation_id.startswith(("get_", "list_", "search_", "resolve_", "query_", "check_", "download_"))


WRITE_OPERATIONS = frozenset(name for name in OPERATIONS if is_write(name))


def operation_for_path(method: str, path: str) -> str:
    for operation in OPERATIONS.values():
        if operation.method == method and operation.path == path:
            return operation.operation_id
    raise ValueError("Unknown PowerContext operation")  # noqa: TRY003
