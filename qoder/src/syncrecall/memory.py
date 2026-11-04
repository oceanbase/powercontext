"""PowerContext boundary: what the application remembers about the learner.

PowerContext is the project's **memory and context service**. It owns everything
that must outlive one request and shape the *next* request:

* ``profile``      — per-user preferences that change retrieval behaviour
                     (courses to search, keyword/vector weights, source types,
                     top-k). Written when the user says "以后检索多给我一些论文"
                     or changes the sliders; read at the start of every query and
                     turned into the structured filter + RRF weights of the
                     hybrid SQL in seekdb.
* ``focus``        — the current study focus, so the assistant can pick the right
                     course even when the question is vague.
* ``learning``     — weak points and wins observed while reviewing, which the
                     revision plan then picks first.
* ``session``      — a short handoff of the last session ("上次复习到 Raft 的提交
                     规则"), so a new session continues instead of restarting.

Design notes
------------
* Each memory is one *kind* in PowerContext with a stable ``reason``; the newest
  active memory of that kind wins. That keeps writes idempotent in spirit and
  makes the "before / after" story easy to show.
* Everything is inside one PowerContext *Scope* per user, so several learners can
  share one Server without seeing each other's memory.
* The HTTP API of PowerContext 1.5.0 is used directly (``/v1/memory/remember``,
  ``/v1/memory/search``, ``/v1/scopes``) so the project runs with a plain
  ``powercontext[cli,server]`` install; the official Python client ships in the
  ``client`` extra.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MEMORY_KINDS = ("profile", "focus", "learning", "session")

# Every memory of a kind starts with a fixed prefix. It is what the application
# searches for, and what it verifies on the way back.
MEMORY_PREFIX = {
    "profile": "检索偏好",
    "focus": "当前聚焦",
    "learning": "学习观察",
    "session": "会话交接",
}
MEMORY_KIND_QUERY = {
    "profile": "检索偏好 profile courses weights top_k tag",
    "focus": "当前聚焦 focus 课程",
    "learning": "学习观察 learning weak strong 薄弱点",
    "session": "会话交接 session 上次 复习进度",
}

DEFAULT_PROFILE: Dict[str, Any] = {
    "courses": [],
    "source_types": [],
    "keyword_weight": 0.6,
    "vector_weight": 0.4,
    "top_k": 8,
    "tag": "",
}

_PROFILE_KEYS = {
    "courses": list,
    "source_types": list,
    "keyword_weight": float,
    "vector_weight": float,
    "top_k": int,
    "tag": str,
}


class PowerContextUnavailable(RuntimeError):
    """Raised when the PowerContext Server cannot be reached."""


@dataclass
class MemoryHit:
    kind: str
    text: str
    score: float
    artifact_id: Optional[str] = None
    revision: Optional[int] = None
    created_at: str = ""


class PowerContextMemory:
    """Thin, explicit client for the PowerContext Server."""

    def __init__(self, base_url: str, *, timeout: float = 5.0, search_limit: int = 50) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.search_limit = search_limit
        self._scope_cache: Dict[str, str] = {}
        self._lock = threading.Lock()

    # -- transport ----------------------------------------------------------
    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:  # pragma: no cover - surfaces in the UI
            detail = error.read().decode("utf-8", errors="replace")
            raise PowerContextUnavailable(f"{path} -> HTTP {error.code}: {detail}") from error
        except (URLError, OSError) as error:  # pragma: no cover - surfaces in the UI
            raise PowerContextUnavailable(f"{path} -> {error}") from error
        return json.loads(body) if body else {}

    def _get(self, path: str) -> Dict[str, Any]:
        request = Request(f"{self.base_url}{path}", method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except (HTTPError, URLError, OSError) as error:  # pragma: no cover
            raise PowerContextUnavailable(f"{path} -> {error}") from error
        return json.loads(body) if body else {}

    # -- health / scope -----------------------------------------------------
    def health(self) -> Dict[str, Any]:
        try:
            ready = self._get("/health/ready")
        except PowerContextUnavailable as error:
            return {"ok": False, "error": str(error), "url": self.base_url}
        return {"ok": True, "url": self.base_url, "ready": ready}

    def scope_for(self, user: str) -> str:
        """Return the scope id for a user, creating it (idempotently) if needed."""
        with self._lock:
            if user in self._scope_cache:
                return self._scope_cache[user]
        scope = self._post(
            "/v1/scopes",
            {
                "title": f"SyncRecall · {user}",
                "summary": "学习资料检索与复习记忆：检索偏好、当前聚焦、薄弱点与会话交接。",
                "idempotency_key": f"syncrecall-scope-{user}",
            },
        )
        scope_id = scope.get("scope_id") or scope.get("id")
        if not scope_id:
            raise PowerContextUnavailable(f"create scope returned no id: {scope}")
        with self._lock:
            self._scope_cache[user] = scope_id
        return scope_id

    # -- memory -------------------------------------------------------------
    def remember(self, user: str, kind: str, text: str, *, reason: str) -> Dict[str, Any]:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {kind!r}; expected one of {MEMORY_KINDS}")
        scope_id = self.scope_for(user)
        response = self._post(
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason},
        )
        records = response.get("records") or []
        return {
            "scope_id": scope_id,
            "kind": kind,
            "changed": response.get("changed"),
            "records": [
                {
                    "artifact_id": (record.get("artifact") or {}).get("artifact_id"),
                    "revision": (record.get("artifact") or {}).get("revision"),
                    "kind": record.get("kind"),
                    "text": record.get("text"),
                    "state": record.get("state"),
                }
                for record in records
            ],
        }

    def _all_of_kind(self, user: str, kind: str) -> List[MemoryHit]:
        """Every active memory of one kind, oldest first.

        PowerContext returns hits ordered by its own relevancy, which is about the
        *query text*, not about time. For a "latest preference wins" policy the
        application therefore reads the whole bucket, attaches the artifact's
        ``created_at`` and orders it itself. The Server stays the system of record;
        the ordering rule stays visible here instead of hiding in a ranking.
        """
        scope_id = self.scope_for(user)
        query = MEMORY_KIND_QUERY.get(kind, kind)
        response = self._post(
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": self.search_limit, "mode": "auto"},
        )
        created = self._artifact_created_at(scope_id)
        matches: List[MemoryHit] = []
        for hit in response.get("hits") or []:
            record = hit.get("memory") or hit
            if record.get("kind") != kind:
                continue
            if record.get("state") not in (None, "active"):
                continue
            text = str(record.get("text") or "")
            # Each kind is written with a fixed prefix, so the prefix is the
            # reliable token that survives the Server's ranking.
            if not text.startswith(MEMORY_PREFIX[kind]):
                continue
            artifact = record.get("artifact") or {}
            artifact_id = artifact.get("artifact_id")
            matches.append(
                MemoryHit(
                    kind=kind,
                    text=text,
                    score=float(hit.get("score") or 0.0),
                    artifact_id=artifact_id,
                    revision=artifact.get("revision"),
                    created_at=created.get(artifact_id, ""),
                )
            )
        matches.sort(key=lambda item: (item.created_at, item.artifact_id or ""))
        return matches

    def _artifact_created_at(self, scope_id: str) -> Dict[str, str]:
        """Map ``artifact_id`` to its creation timestamp, from PowerContext itself."""
        try:
            page = self._get(f"/v1/scopes/{scope_id}/artifacts/atomic-memory?limit=100")
        except PowerContextUnavailable:
            return {}
        mapping: Dict[str, str] = {}
        for item in page.get("items") or []:
            artifact_id = item.get("artifact_id")
            if artifact_id:
                mapping[artifact_id] = str(item.get("created_at") or "")
        return mapping

    def recall(self, user: str, kind: str, limit: int = 5) -> List[MemoryHit]:
        hits = self._all_of_kind(user, kind)
        return hits[-limit:][::-1]

    def latest(self, user: str, kind: str) -> Optional[MemoryHit]:
        hits = self._all_of_kind(user, kind)
        return hits[-1] if hits else None

    def forget_kind(self, user: str, kind: str) -> int:
        """Remove every stored memory of one kind (used by the reset button)."""
        scope_id = self.scope_for(user)
        removed = 0
        while True:
            hits = self.recall(user, kind, limit=50)
            if not hits:
                return removed
            progressed = False
            for hit in hits:
                try:
                    self._post(
                        "/v1/memory/forget",
                        {"scope_id": scope_id, "kind": kind, "text": hit.text},
                    )
                    removed += 1
                    progressed = True
                except PowerContextUnavailable:
                    return removed
            if not progressed:
                return removed

    # -- typed helpers ------------------------------------------------------
    def save_profile(self, user: str, profile: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        cleaned = normalise_profile(profile)
        return self.remember(
            user,
            "profile",
            MEMORY_PREFIX["profile"] + " " + json.dumps(cleaned, ensure_ascii=False, sort_keys=True),
            reason=reason,
        )

    def load_profile(self, user: str) -> Dict[str, Any]:
        hit = self.latest(user, "profile")
        if hit is None:
            return dict(DEFAULT_PROFILE)
        return parse_profile(hit.text, hit)

    def save_focus(self, user: str, focus: str, *, reason: str = "user updated focus") -> Dict[str, Any]:
        return self.remember(user, "focus", f"{MEMORY_PREFIX['focus']} {focus.strip()}", reason=reason)

    def load_focus(self, user: str) -> Optional[str]:
        hit = self.latest(user, "focus")
        if hit is None:
            return None
        return hit.text[len(MEMORY_PREFIX["focus"]) :].strip()

    def save_learning(
        self, user: str, *, course: str, weak: List[str], strong: List[str], reason: str
    ) -> Dict[str, Any]:
        payload = {"course": course, "weak": weak, "strong": strong}
        return self.remember(
            user,
            "learning",
            MEMORY_PREFIX["learning"] + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            reason=reason,
        )

    def load_learning(self, user: str, course: str = "") -> Dict[str, Any]:
        """Return the newest learning observation, for ``course`` when given."""
        candidates = [
            payload
            for payload in (_parse_learning(hit.text) for hit in self._all_of_kind(user, "learning"))
            if payload
        ]
        if not candidates:
            return {"course": course, "weak": [], "strong": []}
        if course:
            for payload in reversed(candidates):
                if payload.get("course") == course:
                    return payload
        return candidates[-1]

    def save_session(self, user: str, summary: str, *, reason: str) -> Dict[str, Any]:
        return self.remember(
            user, "session", f"{MEMORY_PREFIX['session']} {summary.strip()}", reason=reason
        )

    def load_session(self, user: str) -> Optional[str]:
        hit = self.latest(user, "session")
        if hit is None:
            return None
        return hit.text[len(MEMORY_PREFIX["session"]) :].strip()

    def snapshot(self, user: str) -> Dict[str, Any]:
        """Everything the UI shows about this user's memory."""
        started = time.perf_counter()
        profile_hit = self.latest(user, "profile")
        return {
            "scope_id": self.scope_for(user),
            "profile": parse_profile(profile_hit.text, profile_hit) if profile_hit else dict(DEFAULT_PROFILE),
            "profile_source": "powercontext" if profile_hit else "default",
            "profile_text": profile_hit.text if profile_hit else None,
            "focus": self.load_focus(user),
            "learning": self.load_learning(user),
            "last_session": self.load_session(user),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }


# -- parsing helpers ---------------------------------------------------------
def normalise_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only known keys and coerce them, so a bad write cannot poison recall."""
    cleaned = dict(DEFAULT_PROFILE)
    for key, caster in _PROFILE_KEYS.items():
        if key not in profile:
            continue
        value = profile[key]
        try:
            if caster is list:
                if isinstance(value, str):
                    value = [item.strip() for item in value.split(",") if item.strip()]
                cleaned[key] = [str(item) for item in value]
            else:
                cleaned[key] = caster(value)
        except (TypeError, ValueError):
            continue
    cleaned["keyword_weight"] = max(0.0, min(1.0, cleaned["keyword_weight"]))
    cleaned["vector_weight"] = max(0.0, min(1.0, cleaned["vector_weight"]))
    if cleaned["keyword_weight"] + cleaned["vector_weight"] <= 0:
        cleaned["keyword_weight"], cleaned["vector_weight"] = DEFAULT_PROFILE["keyword_weight"], DEFAULT_PROFILE["vector_weight"]
    cleaned["top_k"] = max(1, min(30, cleaned["top_k"]))
    return cleaned


def _pick_json(text: str) -> Optional[Dict[str, Any]]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_profile(text: str, hit: Optional[MemoryHit] = None) -> Dict[str, Any]:
    payload = _pick_json(text)
    if payload is None:
        return dict(DEFAULT_PROFILE)
    return normalise_profile(payload)


def _parse_learning(text: str) -> Optional[Dict[str, Any]]:
    payload = _pick_json(text)
    if not payload or "weak" not in payload:
        return None
    return {
        "course": str(payload.get("course", "")),
        "weak": [str(item) for item in payload.get("weak", [])],
        "strong": [str(item) for item in payload.get("strong", [])],
    }
