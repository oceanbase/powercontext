import ast
from pathlib import Path


def _list_memories_function():
    source = Path("src/server/api/v1/memories.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "list_memories"
        ):
            return node
    raise AssertionError("list_memories function not found")


def test_list_memories_api_builds_scope_metadata_filter():
    function = _list_memories_function()

    filter_assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "filters"
            for target in node.targets
        )
    ]

    assert filter_assignments, "filters assignment not found"
    assert ast.dump(filter_assignments[0].value) == ast.dump(
        ast.parse(
            '{"scope": scope} if scope is not None else None',
            mode="eval",
        ).body
    )


def test_list_memories_api_passes_scope_filter_to_memory_service():
    function = _list_memories_function()
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"count_memories", "list_memories"}
    ]

    assert calls, "service memory calls not found"
    for call in calls:
        filters_keywords = [
            keyword
            for keyword in call.keywords
            if keyword.arg == "filters"
        ]
        assert filters_keywords, f"{call.func.attr} does not pass filters"
        assert isinstance(filters_keywords[0].value, ast.Name)
        assert filters_keywords[0].value.id == "filters"
