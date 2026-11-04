"""Smoke check for the PowerContext memory layer (requires a running Server)."""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from syncrecall.memory import PowerContextMemory  # noqa: E402

memory = PowerContextMemory("http://127.0.0.1:18080")
print("health:", memory.health()["ok"], memory.health().get("ready", {}).get("status"))
user = "demo-user"
print("scope:", memory.scope_for(user))
print("remember:", memory.save_profile(
    user,
    {"courses": ["分布式系统"], "keyword_weight": 0.7, "vector_weight": 0.3, "top_k": 6, "tag": "Raft"},
    reason="smoke test",
))
print("snapshot:", memory.snapshot(user))
print("focus write:", memory.save_focus(user, "分布式系统期末复习"))
print("session write:", memory.save_session(user, "上次停在第 4 周笔记，复习到 Raft 提交。", reason="smoke test"))
print("snapshot2:", memory.snapshot(user))
