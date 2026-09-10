"""Derive Dify's nested request schemas from the typed PC HTTP contract."""

import sys
from pathlib import Path
from typing import Any

import yaml
from powercontext.http import (
    FlushMemoryRequest,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "powercontext"))
from bridge import Observation  # noqa: E402


def inline_schema(value: Any, definitions: dict[str, Any]) -> Any:
    # Dify embeds this schema under properties.request. Inline references so they
    # do not accidentally resolve against the enclosing tool's schema root.
    if isinstance(value, list):
        return [inline_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        name = value["$ref"].removeprefix("#/$defs/")
        value = {**definitions[name], **{key: item for key, item in value.items() if key != "$ref"}}
    return {key: inline_schema(item, definitions) for key, item in value.items() if key not in {"$defs", "title"}}


def generate() -> None:
    for name, model in {
        "prepare_context": PrepareContextRequest,
        "capture_event": Observation,
        "search_memory": SearchMemoryRequest,
        "remember_memory": RememberMemoryRequest,
        "get_memory_entry": GetMemoryEntryRequest,
        "revise_memory_entry": ReviseMemoryEntryRequest,
        "retire_memory_entry": RetireMemoryEntryRequest,
        "flush_memory": FlushMemoryRequest,
    }.items():
        schema = model.model_json_schema()
        schema["properties"].pop("scope_id", None)
        schema["required"] = [key for key in schema.get("required", []) if key != "scope_id"]
        path = ROOT / "powercontext" / "tools" / f"{name}.yaml"
        declaration = yaml.safe_load(path.read_text())
        request = next(parameter for parameter in declaration["parameters"] if parameter["name"] == "request")
        request["input_schema"] = inline_schema(schema, schema.get("$defs", {}))
        path.write_text(yaml.safe_dump(declaration, allow_unicode=True, sort_keys=False, width=100))


if __name__ == "__main__":
    generate()
