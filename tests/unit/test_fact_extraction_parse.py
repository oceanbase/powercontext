"""Tests for parse_fact_extraction_json / parse_memory_actions_json."""

from powermem.utils.utils import (
    normalize_fact_extraction_payload,
    parse_fact_extraction_json,
    parse_memory_actions_json,
)


def test_parse_fact_dict_facts_key():
    assert parse_fact_extraction_json('{"facts": ["a", "b"]}') == ["a", "b"]


def test_parse_memory_actions():
    raw = '{"memory": [{"id": "0", "text": "t", "event": "ADD"}]}'
    actions = parse_memory_actions_json(raw)
    assert len(actions) == 1
    assert actions[0].get("event") == "ADD"


def test_normalize_nested_data():
    assert normalize_fact_extraction_payload({"data": {"facts": ["n"]}}) == ["n"]
