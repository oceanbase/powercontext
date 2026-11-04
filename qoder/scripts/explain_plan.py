"""Print the seekdb execution plan of the hybrid statement.

The plan is the proof that the two branches really run inside the database:

* ``VECTOR INDEX SCAN`` on ``vidx_note`` — the HNSW index on ``embedding``;
* ``TEXT RETRIEVAL SCAN`` on ``ft_note``  — the ``ik`` full-text index.

Usage:  python scripts/explain_plan.py "Raft 怎么保证不丢已提交的日志"
"""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.embedding import embed  # noqa: E402
from syncrecall.config import SETTINGS  # noqa: E402
from syncrecall.search import build_hybrid_sql, fulltext_query  # noqa: E402
from syncrecall.service import extract_keywords  # noqa: E402
from syncrecall.store import DB  # noqa: E402

question = sys.argv[1] if len(sys.argv) > 1 else "Raft 怎么保证不丢已提交的日志"
keywords = extract_keywords(question)
text = fulltext_query(keywords)
vector = embed([text], SETTINGS.embedding_provider, SETTINGS.embedding_model, SETTINGS.embedding_dim)[0]
filters = {"courses": [], "source_types": [], "tag": ""}
built = build_hybrid_sql(text=text, vector=vector, filters=filters, limit=6)

db = DB()
sql = built["sql"]
params = built["params"]
# EXPLAIN does not accept bound weights here, so substitute the two literals.
for value in params[:2]:
    sql = sql.replace("%s", str(value), 1)
for value in params[2:]:
    sql = sql.replace("%s", "'" + str(value).replace("'", "''") + "'", 1)

with db.cursor() as cur:
    cur.execute("EXPLAIN " + sql)
    rows = cur.fetchall()

# seekdb returns the plan as pre-formatted text rows in a single column.
for row in rows:
    print(row[0])
