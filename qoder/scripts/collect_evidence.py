"""Collect the evidence quoted in docs/技术使用记录.md.

Writes docs/evidence/ with:
  explain-plan.txt          seekdb execution plan of the hybrid statement
  hybrid-sql.txt            the exact SQL text that was executed
  memory-trace.json         every PowerContext read/write of one recall
  before-after.json         same question, before/after a preference is remembered
  event-log.json            recent rows of seekdb.event_log
  seekdb-version.txt        server version and the two indexes
"""

from __future__ import annotations

import io
import json
import sys
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.config import SETTINGS  # noqa: E402
from syncrecall.embedding import embed  # noqa: E402
from syncrecall.search import build_hybrid_sql, fulltext_query  # noqa: E402
from syncrecall.service import Service, extract_keywords  # noqa: E402
from syncrecall.store import DB  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "evidence"
OUT.mkdir(parents=True, exist_ok=True)

db = DB()
service = Service()

# --- 1. version + indexes ---------------------------------------------------
lines = [f"seekdb version: {db.version()}"]
with db.cursor() as cur:
    cur.execute("SHOW INDEX FROM note")
    for row in cur.fetchall():
        lines.append(f"index {row[2]}: column={row[4]} type={row[6]}")
    cur.execute("SHOW CREATE TABLE note")
    lines.append("")
    lines.append(cur.fetchone()[1])
(OUT / "seekdb-version.txt").write_text("\n".join(lines), encoding="utf-8")

# --- 2. the SQL + its plan ---------------------------------------------------
question = "Raft 怎么保证不丢已提交的日志"
keywords = extract_keywords(question)
text = fulltext_query(keywords)
vector = embed([text], SETTINGS.embedding_provider, SETTINGS.embedding_model, SETTINGS.embedding_dim)[0]
built = build_hybrid_sql(
    text=text,
    vector=vector,
    filters={"courses": ["分布式系统"], "source_types": ["note"], "tag": ""},
    keyword_weight=0.7,
    vector_weight=0.3,
    limit=6,
)
(OUT / "hybrid-sql.txt").write_text(
    f"-- question: {question}\n-- keywords: {text}\n\n{built['sql']}\n\n-- params: {built['params']}\n",
    encoding="utf-8",
)

explain_sql = built["sql"]
for value in built["params"][:2]:
    explain_sql = explain_sql.replace("%s", str(value), 1)
for value in built["params"][2:]:
    explain_sql = explain_sql.replace("%s", "'" + str(value).replace("'", "''") + "'", 1)
with db.cursor() as cur:
    cur.execute("EXPLAIN " + explain_sql)
    plan = "\n".join(str(row[0]) for row in cur.fetchall())
(OUT / "explain-plan.txt").write_text(plan, encoding="utf-8")

# --- 3. one full recall, with the PowerContext trace ------------------------
user = "evidence-" + uuid.uuid4().hex[:6]
service.set_profile(user, {"courses": ["分布式系统"], "source_types": ["note"], "keyword_weight": 0.7,
                           "vector_weight": 0.3, "top_k": 6, "tag": ""}, reason="evidence: baseline")
result = service.recall(user, question)
(OUT / "memory-trace.json").write_text(
    json.dumps({"question": question, "scope_id": result.scope_id,
                "memory_events": result.memory_events, "hits": result.hits},
               ensure_ascii=False, indent=2),
    encoding="utf-8",
)

# --- 4. before / after the memory changes -----------------------------------
question2 = "Raft 怎么保证不丢已提交的日志"
service.set_profile(user, {"courses": [], "source_types": [], "keyword_weight": 0.6,
                           "vector_weight": 0.4, "top_k": 6, "tag": ""}, reason="evidence: wide")
wide = service.recall(user, question2)
service.set_profile(user, {"courses": ["分布式系统"], "source_types": ["note", "paper"],
                           "keyword_weight": 0.7, "vector_weight": 0.3, "top_k": 6, "tag": ""},
                    reason="evidence: narrow")
narrow = service.recall(user, question2)
(OUT / "before-after.json").write_text(
    json.dumps(
        {
            "question": question2,
            "before": {"profile": wide.profile, "source": wide.profile_source,
                       "hits": [{k: hit[k] for k in ("note_id", "title", "course", "score")} for hit in wide.hits]},
            "after": {"profile": narrow.profile, "source": narrow.profile_source,
                      "hits": [{k: hit[k] for k in ("note_id", "title", "course", "score")} for hit in narrow.hits]},
            "memory_events": narrow.memory_events,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

# --- 5. the audit trail ------------------------------------------------------
(OUT / "event-log.json").write_text(
    json.dumps(db.fetch_events(limit=40), ensure_ascii=False, indent=2, default=str),
    encoding="utf-8",
)

print("wrote:", sorted(path.name for path in OUT.iterdir()))
print("plan operators:")
for line in plan.splitlines():
    if "INDEX" in line or "SCAN" in line:
        print("  ", line.strip())
