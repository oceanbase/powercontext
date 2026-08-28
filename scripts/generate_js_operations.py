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

"""Generate TypeScript HTTP operation tables from OpenAPI."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "openapi" / "powercontext.yaml"
GENERATED_PATHS = (
    ROOT / "integrations" / "dsh" / "plugins" / "powercontext" / "src" / "operations.generated.ts",
    ROOT / "integrations" / "opencode" / "plugins" / "powercontext" / "src" / "operations.generated.ts",
    ROOT / "integrations" / "pi" / "plugins" / "powercontext" / "src" / "operations.generated.ts",
)
DRIFT_MESSAGE = "Generated JS operations drifted; run 'make js-api-generate' and review the result."
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
LICENSE_HEADER = """\
/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

"""


def render_operations_source(doc: dict[str, Any]) -> str:
    rows = parse_operations(doc)
    if not rows:
        raise SystemExit(  # noqa: TRY003
            "generate_js_operations: no operations parsed from openapi/powercontext.yaml"
        )
    body = "\n".join(_render_row(row) for row in rows)
    return (
        f"{LICENSE_HEADER}// generated from openapi/powercontext.yaml; do not edit.\n"
        "\n"
        "export const OPERATIONS = {\n"
        f"{body}\n"
        "} as const\n"
        "\n"
        "export type OperationId = keyof typeof OPERATIONS\n"
        "\n"
        "export type OperationSpec = (typeof OPERATIONS)[OperationId]\n"
        "\n"
        "export const OPERATION_IDS = Object.keys(OPERATIONS) as OperationId[]\n"
    )


def parse_operations(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, path_item in (doc.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict) or not operation.get("operationId"):
                continue
            parameters = _operation_parameters(doc, path_item, operation)
            body_schema = _json_body_schema(doc, operation)
            rows.append({
                "operationId": operation["operationId"],
                "method": method.upper(),
                "path": path,
                "location": _request_location(body_schema, parameters),
                "scope": _operation_has_scope(doc, body_schema, parameters),
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail when generated JS operations have drifted.")
    args = parser.parse_args()
    contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    source = render_operations_source(contract)
    if args.check:
        current = [path.read_text(encoding="utf-8") if path.is_file() else "" for path in GENERATED_PATHS]
        if any(item.replace("\r\n", "\n") != source.replace("\r\n", "\n") for item in current):
            raise SystemExit(DRIFT_MESSAGE)
        return
    for path in GENERATED_PATHS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")


def _render_row(row: dict[str, Any]) -> str:
    location = "null" if row["location"] is None else f'"{row["location"]}"'
    scope = "true" if row["scope"] else "false"
    return (
        f"  {row['operationId']}: {{ method: '{row['method']}', path: '{row['path']}', "
        f"location: {location}, scope: {scope} }},"
    )


def _resolve_ref(doc: dict[str, Any], ref: str, seen: set[str]) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
        return None
    seen.add(ref)
    current: Any = doc
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _deref(doc: dict[str, Any], node: Any, seen: set[str] | None = None) -> Any:
    resolved_seen = seen if seen is not None else set()
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        return _deref(doc, _resolve_ref(doc, ref, resolved_seen), resolved_seen)
    return node


def _schema_has_scope(doc: dict[str, Any], schema: Any, seen: set[str] | None = None) -> bool:
    resolved = _deref(doc, schema, seen or set())
    if not isinstance(resolved, dict):
        return False
    properties = resolved.get("properties")
    if isinstance(properties, dict) and "scope_id" in properties:
        return True
    for key in ("allOf", "oneOf", "anyOf"):
        parts = resolved.get(key)
        if isinstance(parts, list) and any(_schema_has_scope(doc, part, set(seen or set())) for part in parts):
            return True
    return False


def _json_body_schema(doc: dict[str, Any], operation: dict[str, Any]) -> Any:
    body = _deref(doc, operation.get("requestBody"))
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, dict):
        return None
    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return None
    return json_content.get("schema")


def _operation_parameters(
    doc: dict[str, Any],
    path_item: dict[str, Any],
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    listed = [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]
    resolved = [_deref(doc, item) for item in listed]
    return [item for item in resolved if isinstance(item, dict)]


def _request_location(body_schema: Any, parameters: list[dict[str, Any]]) -> str | None:
    if body_schema:
        return "body"
    if any(parameter.get("in") == "query" for parameter in parameters):
        return "query"
    return None


def _operation_has_scope(doc: dict[str, Any], body_schema: Any, parameters: list[dict[str, Any]]) -> bool:
    if body_schema and _schema_has_scope(doc, body_schema):
        return True
    return any(parameter.get("in") == "query" and parameter.get("name") == "scope_id" for parameter in parameters)


if __name__ == "__main__":
    main()
