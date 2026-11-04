"""Tests that need neither seekdb nor PowerContext: parsing and SQL building."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.embedding import HashEmbedder, vector_literal  # noqa: E402
from syncrecall.memory import DEFAULT_PROFILE, normalise_profile, parse_profile  # noqa: E402
from syncrecall.search import build_hybrid_sql, fulltext_query  # noqa: E402
from syncrecall.service import card_id_for, extract_keywords  # noqa: E402


def test_extract_keywords_keeps_latin_terms_and_drops_scaffolding():
    keywords = extract_keywords("Raft 怎么保证不丢已提交的日志")
    assert keywords[0] == "Raft"
    assert "怎么" not in keywords
    assert any("日志" in keyword or "提交" in keyword for keyword in keywords)
    assert all(len(keyword.strip()) >= 2 for keyword in keywords)


def test_extract_keywords_keeps_technical_tokens():
    keywords = extract_keywords("为什么 InnoDB 可重复读下 update 后还能看到别人的修改")
    assert "InnoDB" in keywords
    assert "update" in keywords
    assert "2PC" in extract_keywords("2PC 的阻塞点是什么")


def test_fulltext_query_drops_quoting_characters():
    assert fulltext_query(["Raft", "Raft", "日志"]) == "Raft 日志"
    assert "'" not in fulltext_query(["it's", 'a"b'])


def test_build_hybrid_sql_is_one_statement_with_both_branches():
    built = build_hybrid_sql(
        text="Raft 日志",
        vector=[0.1, 0.2, 0.3],
        filters={"courses": ["分布式系统"], "source_types": ["note"], "tag": "Raft"},
        keyword_weight=0.7,
        vector_weight=0.3,
        limit=5,
    )
    sql = built["sql"]
    assert "MATCH(title, body) AGAINST ('Raft 日志' IN NATURAL LANGUAGE MODE)" in sql
    assert "cosine_distance(embedding, '[0.100000,0.200000,0.300000]')" in sql
    assert "APPROXIMATE" in sql
    assert "course IN (%s)" in sql
    assert "source_type IN (%s)" in sql
    assert "CONCAT(',', tags, ',') LIKE %s" in sql
    assert "ORDER BY fused_score DESC" in sql
    assert sql.count(";") == 0  # exactly one statement, no stacking
    # weights first, then the positional filters
    assert built["params"][:2] == [0.7, 0.3]
    assert built["params"][2:] == ["分布式系统", "note", "%,Raft,%"]


def test_build_hybrid_sql_without_filters_has_no_where_clause():
    built = build_hybrid_sql(text="Raft", vector=[0.0, 1.0], limit=3)
    assert "WHERE course" not in built["sql"]
    assert "LIMIT 3" in built["sql"]


def test_profile_normalisation_clamps_and_defaults():
    profile = normalise_profile(
        {"courses": "分布式系统, 数据库系统", "keyword_weight": 2, "vector_weight": -1, "top_k": 999, "bogus": 1}
    )
    assert profile["courses"] == ["分布式系统", "数据库系统"]
    assert profile["keyword_weight"] == 1.0
    assert profile["vector_weight"] == 0.0
    assert profile["top_k"] == 30
    assert "bogus" not in profile


def test_profile_with_both_weights_zero_falls_back_to_defaults():
    profile = normalise_profile({"keyword_weight": 0, "vector_weight": 0})
    assert profile["keyword_weight"] == DEFAULT_PROFILE["keyword_weight"]


def test_parse_profile_ignores_unreadable_text():
    assert parse_profile("检索偏好 not-json") == DEFAULT_PROFILE
    parsed = parse_profile('检索偏好 {"courses": ["分布式系统"], "top_k": 4}')
    assert parsed["courses"] == ["分布式系统"]
    assert parsed["top_k"] == 4


def test_hash_embedder_is_deterministic_and_normalised():
    embedder = HashEmbedder(dim=64)
    first, second = embedder.encode(["Raft 日志复制", "Raft 日志复制"])
    assert first == second
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9
    assert len(vector_literal(first).split(",")) == 64


def test_card_id_is_stable():
    assert card_id_for("note-1", "问什么") == card_id_for("note-1", "问什么")
    assert card_id_for("note-1", "问什么") != card_id_for("note-1", "问别的")
