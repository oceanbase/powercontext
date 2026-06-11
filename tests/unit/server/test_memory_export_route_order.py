from pathlib import Path


def test_export_route_is_defined_before_memory_id_route():
    source = Path("src/server/api/v1/memories.py").read_text(encoding="utf-8")

    assert source.index('@router.get(\n    "/export"') < source.index(
        '@router.get(\n    "/{memory_id}"'
    )
