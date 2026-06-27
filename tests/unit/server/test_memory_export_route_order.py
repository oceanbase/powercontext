from pathlib import Path


def test_export_route_is_defined_before_memory_id_route():
    source = Path("src/server/api/v1/memories.py").read_text(encoding="utf-8")

    assert source.index('@router.get(\n    "/export"') < source.index(
        '@router.get(\n    "/{memory_id}"'
    )


def test_session_timeline_routes_are_defined_before_memory_id_route():
    source = Path("src/server/api/v1/memories.py").read_text(encoding="utf-8")
    memory_id_route = source.index('@router.get(\n    "/{memory_id}"')

    for route in ("/sessions", "/session-stats", "/timeline"):
        assert source.index(f'@router.get(\n    "{route}"') < memory_id_route


def test_import_route_calls_memory_import_with_supported_signature():
    source = Path("src/server/api/v1/memories.py").read_text(encoding="utf-8")
    import_route = source[
        source.index("async def import_memories") : source.index(
            'message=f"Import completed'
        )
    ]

    assert "is_file" not in import_route
