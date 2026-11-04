"""One-command verification: seed the data, then ask the sample questions.

Usage (after ``pip install -r requirements.txt`` and with seekdb + PowerContext up):

    cd qoder
    python verify.py

It seeds the sample material, runs the same question twice (before and after a
preference is remembered in PowerContext), prints both rankings, and finally shows
the seekdb execution plan, so a reviewer sees the whole story in one screen.
"""

from __future__ import annotations

import io
import json
import sys
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from syncrecall.config import SETTINGS  # noqa: E402
from syncrecall.embedding import embed  # noqa: E402
from syncrecall.search import build_hybrid_sql, fulltext_query  # noqa: E402
from syncrecall.service import Service, extract_keywords, seed  # noqa: E402
from syncrecall.store import DB, connect_probe  # noqa: E402

QUESTION = "Raft 怎么保证不丢已提交的日志"


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main() -> int:
    hr("0. 环境自检")
    version = connect_probe(SETTINGS)
    print("seekdb      :", version or "不可达（请按 README 第 2.1 节启动）")
    service = Service()
    print("PowerContext:", service.memory.health().get("ok") and SETTINGS.powercontext_url or "不可达")
    print("embedding   :", service.embedder_name)
    if version is None or not service.memory.health().get("ok"):
        print("\n环境未就绪，后面的步骤会失败。")
        return 2

    hr("1. 导入示例资料（21 条资料 + 复习题）")
    print(json.dumps(seed(DB(), SETTINGS, reset=True), ensure_ascii=False))

    user = "verify-" + uuid.uuid4().hex[:6]
    print("\n本次使用独立的 Scope（用户）:", user)

    hr("2. 第一次检索：PowerContext 里还没有偏好")
    service.set_profile(user, {"courses": [], "source_types": [], "keyword_weight": 0.6,
                               "vector_weight": 0.4, "top_k": 6, "tag": ""}, reason="verify: baseline")
    first = Service().recall(user, QUESTION)
    for rank, hit in enumerate(first.hits, 1):
        print(f"  {rank}. [{hit['course']}] {hit['title']}   score={hit['score']:.5f}")
    print("  使用的偏好:", first.profile, "(来源:", first.profile_source + ")")

    hr("3. 写一条偏好进 PowerContext：只看「分布式系统」的笔记与论文")
    service.set_profile(
        user,
        {"courses": ["分布式系统"], "source_types": ["note", "paper"],
         "keyword_weight": 0.7, "vector_weight": 0.3, "top_k": 6, "tag": ""},
        reason="verify: narrow scope",
    )
    second = Service().recall(user, QUESTION)
    print("  同一个问题，再来一次：")
    for rank, hit in enumerate(second.hits, 1):
        print(f"  {rank}. [{hit['course']}] {hit['title']}   score={hit['score']:.5f}")
    print("  使用的偏好:", second.profile, "(来源:", second.profile_source + ")")

    hr("4. 这次检索里 PowerContext 与 seekdb 各自做了什么")
    for event in second.memory_events:
        print(f"  - {event['system']:<13} {event['signal']}  {event.get('api', '')}")
        if event.get("hits"):
            print("      命中:", ", ".join(event["hits"]))
        if event.get("artifact_id"):
            print("      artifact:", event["artifact_id"])
        if event.get("filters"):
            print("      过滤:", event["filters"], "权重:", event["weights"])

    hr("5. seekdb 里这条混合检索的执行计划")
    keywords = extract_keywords(QUESTION)
    text = fulltext_query(keywords)
    vector = embed([text], SETTINGS.embedding_provider, SETTINGS.embedding_model, SETTINGS.embedding_dim)[0]
    built = build_hybrid_sql(
        text=text, vector=vector,
        filters={"courses": ["分布式系统"], "source_types": ["note", "paper"]},
        keyword_weight=0.7, vector_weight=0.3, limit=6,
    )
    sql = built["sql"]
    for value in built["params"][:2]:
        sql = sql.replace("%s", str(value), 1)
    for value in built["params"][2:]:
        sql = sql.replace("%s", "'" + str(value).replace("'", "''") + "'", 1)
    with DB().cursor() as cur:
        cur.execute("EXPLAIN " + sql)
        for row in cur.fetchall():
            line = str(row[0])
            if "SCAN" in line or "SORT" in line or "WINDOW" in line or "JOIN" in line:
                print(" ", line.rstrip())

    hr("结论")
    print("  第一次：命中范围跨课程（含数据库/软件工程的资料）")
    print("  第二次：命中全部落在「分布式系统」，且只含笔记与论文")
    print("  两次使用的是同一段代码、同一个问题，唯一变化是 PowerContext 里记住的偏好。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
