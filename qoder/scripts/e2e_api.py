"""End-to-end API check: bootstrap -> recall -> profile change -> recall again."""

import io
import json
import sys
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8788"


def call(path, payload=None, method=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data else "GET"),
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def show(data):
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])


user = "demo-user"
print("== bootstrap ==")
boot = call(f"/api/bootstrap?user={user}")
print("notes:", boot["notes"], "cards:", boot["cards"], "embedder:", boot["embedder"])
print("courses:", boot["courses"])
print("memory:", json.dumps(boot["memory"], ensure_ascii=False)[:400])

print("\n== reset profile to all courses ==")
show(call("/api/profile", {"user": user, "courses": [], "source_types": [], "keyword_weight": 0.6,
                           "vector_weight": 0.4, "top_k": 8, "tag": "", "reason": "e2e reset"}))

print("\n== recall #1 (no filter) ==")
first = call("/api/recall", {"user": user, "question": "B+ 树为什么会页分裂"})
for hit in first["hits"]:
    print(f"  {hit['score']:.5f} {hit['note_id']:<24} {hit['title']}")
print("  profile used:", first["profile"], "from", first["profile_source"])

print("\n== write preference: only 分布式系统 course ==")
call("/api/profile", {"user": user, "courses": ["分布式系统"], "source_types": [], "keyword_weight": 0.6,
                      "vector_weight": 0.4, "top_k": 8, "tag": "", "reason": "e2e narrow to one course"})

print("\n== recall #2 (same question, filter now remembered) ==")
second = call("/api/recall", {"user": user, "question": "B+ 树为什么会页分裂"})
for hit in second["hits"]:
    print(f"  {hit['score']:.5f} {hit['note_id']:<24} {hit['title']} [{hit['course']}]")
print("  profile used:", second["profile"], "from", second["profile_source"])

print("\n== review plan ==")
plan = call("/api/review?" + urllib.parse.urlencode({"user": user, "course": "分布式系统"}))
print("  due:", plan["due_count"], "/", plan["card_count"], "weak:", plan["weak_points"])
for item in plan["plan"][:3]:
    print("  -", item["title"], "|", item["question"][:60], "| weak:", item["from_weak_point"])

if plan["plan"]:
    print("\n== grade a card 'again' ==")
    graded = call("/api/review/grade", {"user": user, "card_id": plan["plan"][0]["card_id"], "grade": 0})
    show(graded)
    after = call(f"/api/profile?user={user}")
    print("  learning memory:", after["learning"])

print("\n== events ==")
events = call(f"/api/events?user={user}&limit=8")
for event in events["events"]:
    print(" ", event["created_at"], event["action"], event["actor"], f"{event['elapsed_ms']}ms")
