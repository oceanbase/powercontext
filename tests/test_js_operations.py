from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_js_operations",
        REPO_ROOT / "scripts" / "generate_js_operations.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_js_operations_cover_every_openapi_operation() -> None:
    generator = _load_generator()
    doc = yaml.safe_load(generator.CONTRACT_PATH.read_text(encoding="utf-8"))
    rows = generator.parse_operations(doc)
    assert rows
    assert {row["operationId"] for row in rows} == {
        operation["operationId"]
        for path_item in (doc.get("paths") or {}).values()
        if isinstance(path_item, dict)
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }


def test_js_operations_record_method_path_location_and_scope() -> None:
    generator = _load_generator()
    doc = yaml.safe_load(generator.CONTRACT_PATH.read_text(encoding="utf-8"))
    by_id = {row["operationId"]: row for row in generator.parse_operations(doc)}
    assert by_id["get_liveness"] == {
        "operationId": "get_liveness",
        "method": "GET",
        "path": "/health/live",
        "location": None,
        "scope": False,
    }
    assert by_id["get_stats"]["location"] == "query"
    assert by_id["remember_memory"]["location"] == "body"
    assert by_id["remember_memory"]["scope"] is True


def test_committed_js_operations_match_openapi() -> None:
    generator = _load_generator()
    doc = yaml.safe_load(generator.CONTRACT_PATH.read_text(encoding="utf-8"))
    assert generator.GENERATED_PATH.is_file()
    committed = generator.GENERATED_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == generator.render_operations_source(doc)
