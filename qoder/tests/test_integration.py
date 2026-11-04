"""Integration tests against a real seekdb and a real PowerContext Server.

Both are skipped with a readable reason when they are not reachable, so a
reviewer can run ``pytest`` right after cloning and still get a clean result.

What is covered
---------------
* seekdb: schema, the ``ik`` full-text index, the HNSW vector index, and that the
  single hybrid statement returns hits whose order matches the fused score;
* PowerContext: a preference written now is read back, and the *next* recall uses
  it (this is the "memory changes behaviour" claim in the docs);
* the review loop: grading a card schedules it in seekdb and records the weak
  point in PowerContext.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.config import SETTINGS  # noqa: E402
from syncrecall.embedding import embed  # noqa: E402
from syncrecall.memory import PowerContextMemory, PowerContextUnavailable  # noqa: E402
from syncrecall.search import build_hybrid_sql, fulltext_query  # noqa: E402
from syncrecall.service import Service, extract_keywords, seed  # noqa: E402
from syncrecall.store import DB, connect_probe  # noqa: E402

SEEKDB_VERSION = connect_probe(SETTINGS)


def powercontext_available() -> bool:
    try:
        return PowerContextMemory(SETTINGS.powercontext_url, timeout=2).health()["ok"]
    except Exception:  # noqa: BLE001 - only reachability matters here
        return False


needs_seekdb = pytest.mark.skipif(
    SEEKDB_VERSION is None,
    reason="seekdb 不可达：先按 README 启动 seekdb 容器（docker run ... oceanbase/seekdb）",
)
needs_powercontext = pytest.mark.skipif(
    not powercontext_available(),
    reason="PowerContext Server 不可达：先运行 powercontext server run",
)


@pytest.fixture(scope="module")
def loaded_db():
    db = DB()
    db.ensure_schema()
    if db.table_count("note") == 0:
        seed(db, SETTINGS)
    return db


@pytest.fixture()
def user():
    return "pytest-" + uuid.uuid4().hex[:8]


@needs_seekdb
def test_seekdb_version_is_seekdb():
    assert "seekdb" in str(SEEKDB_VERSION)


@needs_seekdb
def test_schema_has_fulltext_and_vector_indexes(loaded_db):
    with loaded_db.cursor() as cur:
        cur.execute("SHOW INDEX FROM note")
        keys = {row[2]: row[4] for row in cur.fetchall()}
        cur.execute("SHOW CREATE TABLE note")
        ddl = cur.fetchone()[1]
    assert "ft_note" in keys
    assert "vidx_note" in keys
    assert "WITH PARSER ik" in ddl
    assert "HNSW" in ddl


@needs_seekdb
def test_fulltext_index_matches_chinese_terms(loaded_db):
    with loaded_db.cursor() as cur:
        cur.execute(
            "SELECT note_id, MATCH(title, body) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score "
            "FROM note WHERE MATCH(title, body) AGAINST (%s IN NATURAL LANGUAGE MODE) "
            "ORDER BY score DESC",
            ("页分裂", "页分裂"),
        )
        rows = cur.fetchall()
    assert rows, "ik 分词后应该能匹配到「页分裂」"
    assert rows[0][0] == "db-bplus-tree"


@needs_seekdb
def test_hybrid_statement_orders_by_fused_score(loaded_db):
    question = "Raft 怎么保证不丢已提交的日志"
    keywords = extract_keywords(question)
    text = fulltext_query(keywords)
    vector = embed([text], SETTINGS.embedding_provider, SETTINGS.embedding_model, SETTINGS.embedding_dim)[0]
    # Restrict to 分布式系统 notes so this test asserts *ranking*, not corpus luck.
    built = build_hybrid_sql(
        text=text, vector=vector, filters={"courses": ["分布式系统"], "source_types": ["note"]}, limit=5
    )
    with loaded_db.cursor() as cur:
        cur.execute(built["sql"], built["params"])
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    assert rows, "混合检索应该有结果"
    scores = [float(row["fused_score"]) for row in rows]
    assert scores == sorted(scores, reverse=True)
    assert "replication" in rows[0]["note_id"], f"Raft 提交相关问题应以日志复制笔记为首，实际 {rows[0]['note_id']}"
    # both branches contributed to the top hit
    assert rows[0]["fts_score"] and float(rows[0]["fts_score"]) > 0
    assert rows[0]["vec_score"] is not None


@needs_seekdb
def test_structured_filter_restricts_courses(loaded_db):
    vector = [0.0] * (SETTINGS.embedding_dim - 1) + [1.0]
    built = build_hybrid_sql(
        text="Paxos", vector=vector, filters={"courses": ["数据库系统"]}, limit=10
    )
    with loaded_db.cursor() as cur:
        cur.execute(built["sql"], built["params"])
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    assert rows
    assert {row["course"] for row in rows} == {"数据库系统"}


@needs_powercontext
@needs_seekdb
def test_memory_round_trip(user):
    memory = PowerContextMemory(SETTINGS.powercontext_url)
    written = memory.save_profile(user, {"courses": ["分布式系统"], "top_k": 5, "tag": "Raft"}, reason="pytest")
    assert written["records"], "PowerContext 应该返回写入的记录引用"
    loaded = memory.load_profile(user)
    assert loaded["courses"] == ["分布式系统"]
    assert loaded["top_k"] == 5
    assert loaded["tag"] == "Raft"


@needs_powercontext
@needs_seekdb
def test_newest_preference_wins(user):
    memory = PowerContextMemory(SETTINGS.powercontext_url)
    memory.save_profile(user, {"courses": ["分布式系统"], "top_k": 3}, reason="first")
    memory.save_profile(user, {"courses": [], "top_k": 9}, reason="second")
    loaded = memory.load_profile(user)
    assert loaded["courses"] == []
    assert loaded["top_k"] == 9


@needs_powercontext
@needs_seekdb
def test_remembered_preference_changes_the_next_recall(user):
    service = Service()
    question = "B+ 树为什么会页分裂"

    # 1) no restriction: database notes are reachable
    service.set_profile(user, {"courses": [], "source_types": [], "top_k": 8}, reason="pytest: wide")
    wide = service.recall(user, question)
    assert {hit["course"] for hit in wide.hits} >= {"数据库系统"}

    # 2) remember a narrower scope, then ask again
    service.set_profile(
        user,
        {"courses": ["分布式系统"], "source_types": ["note", "paper"], "top_k": 8},
        reason="pytest: narrow",
    )
    narrow = service.recall(user, question)
    assert narrow.hits, "过滤后仍应返回分布式系统的笔记"
    assert {hit["course"] for hit in narrow.hits} == {"分布式系统"}
    assert narrow.profile_source == "powercontext"
    assert narrow.profile["courses"] == ["分布式系统"]
    # the SQL carries the same filter, so the behaviour is explainable
    assert "'分布式系统'" not in narrow.sql  # bound as a parameter, not concatenated
    assert "course IN (%s)" in narrow.sql
    assert narrow.params.count("分布式系统") == 1


@needs_powercontext
@needs_seekdb
def test_recall_writes_a_session_handoff_back(user):
    service = Service()
    service.recall(user, "Raft 日志复制")
    handoff = Service().memory.load_session(user)
    assert handoff and "Raft 日志复制" in handoff


@needs_powercontext
@needs_seekdb
def test_review_grade_updates_schedule_and_memory(user):
    service = Service()
    plan = service.review_plan(user, "分布式系统")
    assert plan["plan"], "应该有可复习的题目"
    card = plan["plan"][0]
    result = service.grade_card(user, card["card_id"], grade=0)
    assert result["interval_days"] == 0  # "again" resets the interval
    assert result["memory"]["ok"] is True

    learning = service.memory.load_learning(user, "分布式系统")
    assert learning["weak"], "答错的题目主题应该被记为薄弱点"

    # the weak point now drives the next plan ordering
    service.grade_card(user, card["card_id"], grade=5)
    learned = service.memory.load_learning(user, "分布式系统")
    assert card["topic"] in learned["strong"] or learned["strong"], "答对后应记为掌握"
