"""Smoke check: create the schema and read back its definition."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.store import DB  # noqa: E402

db = DB()
print("version:", db.version())
db.ensure_schema()
with db.cursor() as cur:
    cur.execute("SHOW INDEX FROM note")
    for row in cur.fetchall():
        print("index:", row[2], row[4])
    cur.execute("DESCRIBE note")
    for row in cur.fetchall():
        print("column:", row[0], row[1])
print("counts:", db.table_count("note"), db.table_count("review_card"), db.table_count("event_log"))
