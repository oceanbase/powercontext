"""Pretty-print one recall result (used while developing and in the demo script)."""

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.service import Service  # noqa: E402

question = sys.argv[1] if len(sys.argv) > 1 else "Raft 怎么保证不丢已提交的日志"
user = sys.argv[2] if len(sys.argv) > 2 else "demo-user"

result = Service().recall(user, question)
data = result.as_dict()
print("question :", data["question"])
print("keywords :", data["keywords"])
print("embedder :", data["embedder"])
print("elapsed  :", data["elapsed_ms"], "ms")
print("profile  :", data["profile"], "from", data["profile_source"])
print("hits:")
for hit in data["hits"]:
    print(f"  {hit['score']:.5f}  fts={hit['fts_score']}  vec={hit['vec_score']}  "
          f"{hit['note_id']:<24} {hit['title']}")
print("memory events:")
for event in data["memory_events"]:
    print("  -", event.get("system"), event.get("signal"), event.get("artifact_id") or event.get("hits"))
print("\nSQL:\n" + data["sql"])
