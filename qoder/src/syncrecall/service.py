"""The product: answer a question from personal material, then turn it into review.

Two distinct jobs, two distinct technologies:

``recall(question)``
    1. read the user profile from **PowerContext** (courses, weights, filter tag);
    2. embed the question locally;
    3. run **one hybrid SQL statement in seekdb** (full-text + vector + structured
       filter, fused with weighted RRF);
    4. write a *session handoff* memory back to PowerContext, so the next session
       starts where this one stopped;
    5. return the hits, the citations, the SQL that produced them and the memory
       events that shaped it.

``plan_review(question_id)``
    1. read the weak points of the course from **PowerContext**;
    2. pick review cards from **seekdb** by due date, but weight the question
       generation towards the weak points;
    3. when the session ends, write the observed weak / strong points back to
       PowerContext and schedule the next review in seekdb.

This module is the only place that knows about both systems, which keeps the
separation of duties easy to audit.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from .config import SETTINGS, Settings
from .embedding import embed, get_embedder
from .memory import PowerContextMemory, PowerContextUnavailable, normalise_profile
from .search import fulltext_query, hybrid_search
from .store import DB

_STOPWORDS = set(
    """
    的 了 是 在 和 与 及 或 也 就 都 而 还是 什么 怎么 为什么 如何 哪些 哪个 请 帮 我 你 他
    它 这 那 有 没有 要 会 能 可以 一个 一下 我们 他们 以及 但是 如果 因为 所以 然后
    the a an of to in on for and or is are was were what how why which that this
    """.split()
)

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,6}")
# Question scaffolding that carries no retrieval signal in Chinese.
_CJK_STOP_RUNS = (
    "怎么", "怎样", "如何", "什么", "哪些", "哪个", "为什么", "是不是", "有没有", "请问",
    "帮我", "可以", "能不能", "应该", "需要", "一下", "一个", "这个", "那个", "我们",
    "他们", "以及", "但是", "如果", "因为", "所以", "然后", "还是", "就是", "到底",
    "区别", "其实", "之后", "之前", "里面", "之间", "保证", "能不能", "是不是", "麻烦",
)
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+\-]*")


def extract_keywords(question: str, limit: int = 8) -> List[str]:
    """Cheap, dependency-free keyword extraction for the FTS branch.

    ``ik`` does the real segmentation inside seekdb; this only decides *which*
    terms to hand to ``MATCH ... AGAINST``. Latin tokens are kept whole; Chinese
    runs are stripped of question scaffolding and cut into overlapping 2–4
    character windows, which is what ``ik`` will match against. For Chinese a
    single character carries almost no signal, so no 1-character term is emitted.
    """
    keywords: List[str] = []
    seen_latin: set = set()
    for token in _LATIN.findall(question or ""):
        lowered = token.lower()
        if lowered in _STOPWORDS or token in keywords:
            continue
        if lowered in seen_latin:
            continue  # "PC" from "2PC" adds nothing once "2PC" is present
        seen_latin.add(lowered)
        keywords.append(token)

    cleaned = question or ""
    for run in _CJK_STOP_RUNS:
        cleaned = cleaned.replace(run, " ")
    for word in _STOPWORDS:
        cleaned = cleaned.replace(word, " ")

    for run in _CJK_RUN.findall(cleaned):
        run = run.strip()
        if len(run) < 2 or run in keywords:
            continue
        keywords.append(run)
        for index in range(len(run) - 2):
            piece = run[index : index + 3]
            if piece not in keywords:
                keywords.append(piece)
    return keywords[:limit]


@dataclass
class RecallResult:
    question: str
    hits: List[Dict[str, Any]]
    keywords: List[str]
    sql: str
    params: List[Any]
    elapsed_ms: int
    profile: Dict[str, Any]
    profile_source: str
    memory_events: List[Dict[str, Any]]
    scope_id: str
    embedder: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "hits": self.hits,
            "citations": [
                {"note_id": hit["note_id"], "title": hit["title"], "course": hit["course"]}
                for hit in self.hits
            ],
            "keywords": self.keywords,
            "sql": self.sql,
            "params": self.params,
            "elapsed_ms": self.elapsed_ms,
            "profile": self.profile,
            "profile_source": self.profile_source,
            "memory_events": self.memory_events,
            "scope_id": self.scope_id,
            "embedder": self.embedder,
            "tool_chain": [
                "PowerContext 读取检索偏好 (memory/search)",
                "本地嵌入模型生成查询向量",
                "seekdb 单条 SQL 混合检索 (MATCH + cosine_distance + 结构化过滤 + RRF)",
                "PowerContext 写回会话交接 (memory/remember)",
            ],
        }


class Service:
    """Application service shared by the HTTP API and the CLI."""

    def __init__(self, settings: Settings = SETTINGS, *, memory: Optional[PowerContextMemory] = None,
                 db: Optional[DB] = None) -> None:
        self.settings = settings
        self.memory = memory or PowerContextMemory(settings.powercontext_url)
        self.db = db or DB(settings)
        self._embedder_name = get_embedder(
            settings.embedding_provider, settings.embedding_model, settings.embedding_dim
        ).name

    # -- helpers ------------------------------------------------------------
    @property
    def embedder_name(self) -> str:
        return self._embedder_name

    def _embed_one(self, text: str) -> List[float]:
        vectors = embed([text], self.settings.embedding_provider,
                        self.settings.embedding_model, self.settings.embedding_dim)
        return vectors[0]

    def health(self) -> Dict[str, Any]:
        from .store import connect_probe  # local import keeps the module cycle free

        version = connect_probe(self.settings)
        powercontext = self.memory.health()
        return {
            "seekdb": {"ok": version is not None, "version": version},
            "powercontext": powercontext,
            "embedding": {"provider": self._embedder_name, "dim": self.settings.embedding_dim},
            "ok": version is not None and bool(powercontext.get("ok")),
        }

    # -- recall -------------------------------------------------------------
    def recall(self, user: str, question: str, *, limit: Optional[int] = None,
               save_session: bool = True) -> RecallResult:
        started = time.perf_counter()
        events: List[Dict[str, Any]] = []

        # 1) memory -> retrieval parameters
        try:
            scope_id = self.memory.scope_for(user)
            profile_hit = self.memory.latest(user, "profile")
        except PowerContextUnavailable as error:
            raise RuntimeError(f"PowerContext 不可用：{error}") from error
        profile = (
            normalise_profile(json.loads(_json_payload(profile_hit.text)))
            if profile_hit and _json_payload(profile_hit.text)
            else normalise_profile({})
        )
        profile_source = "powercontext" if profile_hit else "default"
        events.append(
            {
                "signal": "读取检索偏好",
                "system": "PowerContext",
                "api": "POST /v1/memory/search",
                "scope_id": scope_id,
                "result": profile,
                "source": profile_source,
            }
        )

        # 2) local embedding + 3) hybrid SQL in seekdb
        keywords = extract_keywords(question)
        filters = {
            "courses": profile.get("courses") or [],
            "source_types": profile.get("source_types") or [],
            "tag": profile.get("tag") or "",
        }
        vector = self._embed_one(fulltext_query(keywords) or question)
        search = hybrid_search(
            self.db,
            query=question,
            keywords=keywords,
            vector=vector,
            filters=filters,
            keyword_weight=profile["keyword_weight"],
            vector_weight=profile["vector_weight"],
            limit=limit or profile["top_k"],
            scope_id=scope_id,
            actor=user,
        )
        events.append(
            {
                "signal": "混合检索",
                "system": "seekdb",
                "api": "SQL: MATCH ... AGAINST + cosine_distance ... APPROXIMATE + RRF",
                "filters": filters,
                "weights": [profile["keyword_weight"], profile["vector_weight"]],
                "hits": [hit["note_id"] for hit in search["hits"]],
                "elapsed_ms": search["elapsed_ms"],
            }
        )

        # 4) write the handoff back so the next session continues
        if save_session and search["hits"]:
            top = search["hits"][0]
            summary = (
                f"{datetime.now():%m-%d %H:%M} 检索「{question[:40]}」，"
                f"最相关的是《{top['title']}》（{top['course']}），共 {len(search['hits'])} 条命中。"
            )
            try:
                written = self.memory.save_session(user, summary, reason="recall 自动写回会话交接")
                events.append(
                    {
                        "signal": "写回会话交接",
                        "system": "PowerContext",
                        "api": "POST /v1/memory/remember",
                        "kind": "session",
                        "artifact_id": (written["records"] or [{}])[0].get("artifact_id"),
                        "changed": written.get("changed"),
                        "text": summary,
                    }
                )
            except PowerContextUnavailable as error:
                events.append(
                    {"signal": "写回会话交接", "system": "PowerContext", "error": str(error)}
                )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        self.db.log(
            user,
            "recall",
            scope_id=scope_id,
            detail={"question": question, "keywords": keywords, "profile": profile,
                    "hits": [hit["note_id"] for hit in search["hits"]]},
            elapsed_ms=elapsed_ms,
        )
        return RecallResult(
            question=question,
            hits=search["hits"],
            keywords=keywords,
            sql=search["sql"],
            params=search["params"],
            elapsed_ms=elapsed_ms,
            profile=profile,
            profile_source=profile_source,
            memory_events=events,
            scope_id=scope_id,
            embedder=self._embedder_name,
        )

    # -- review plan --------------------------------------------------------
    def review_plan(self, user: str, course: str = "") -> Dict[str, Any]:
        """Pick what to review: seekdb owns the schedule, PowerContext the focus."""
        try:
            scope_id = self.memory.scope_for(user)
            learning = self.memory.load_learning(user, course)
            last_session = self.memory.load_session(user)
        except PowerContextUnavailable as error:
            raise RuntimeError(f"PowerContext 不可用：{error}") from error

        cards = self.db.fetch_cards()
        notes = {note["note_id"]: note for note in self.db.fetch_notes()}
        now = datetime.now()
        weak = set(learning.get("weak") or [])
        scored = []
        for card in cards:
            note = notes.get(card["note_id"], {})
            if course and note.get("course") != course:
                continue
            overdue = card["next_review_at"] <= now
            weak_bonus = 2 if any(keyword and keyword in card["question"] for keyword in weak) else 0
            overdue_seconds = max(0.0, (now - card["next_review_at"]).total_seconds())
            scored.append(
                {
                    "card": card,
                    "note": note,
                    "overdue": overdue,
                    "weak_bonus": weak_bonus,
                    "score": weak_bonus * 1e9 + overdue_seconds,
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        selected = [item for item in scored if item["overdue"]][:8] or scored[:5]

        plan = [
            {
                "card_id": item["card"]["card_id"],
                "note_id": item["card"]["note_id"],
                "course": item["note"].get("course", ""),
                "topic": item["note"].get("topic", ""),
                "title": item["note"].get("title", ""),
                "question": item["card"]["question"],
                "answer": item["card"]["answer"],
                "overdue": item["overdue"],
                "from_weak_point": bool(item["weak_bonus"]),
                "next_review_at": str(item["card"]["next_review_at"]),
            }
            for item in selected
        ]
        return {
            "plan": plan,
            "weak_points": learning.get("weak") or [],
            "strong_points": learning.get("strong") or [],
            "last_session": last_session,
            "scope_id": scope_id,
            "due_count": sum(1 for card in cards if card["next_review_at"] <= now),
            "card_count": len(cards),
        }

    def grade_card(self, user: str, card_id: str, grade: int) -> Dict[str, Any]:
        """SM-2 style scheduling; grade 0=again, 3=hard, 4=good, 5=easy."""
        cards = {card["card_id"]: card for card in self.db.fetch_cards()}
        if card_id not in cards:
            raise KeyError(f"unknown card {card_id}")
        card = cards[card_id]
        ease = float(card["ease"] or 2.5)
        interval = int(card["interval_days"] or 0)
        repetitions = int(card["repetitions"] or 0)

        if grade < 3:
            repetitions = 0
            interval = 0
        else:
            if repetitions == 0:
                interval = 1
            elif repetitions == 1:
                interval = 3
            else:
                interval = max(1, int(round(interval * ease)))
            repetitions += 1
        ease = max(1.3, ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))
        next_at = datetime.now() + timedelta(days=interval, minutes=10 if grade < 3 else 0)

        self.db.update_card(
            card_id,
            ease=round(ease, 3),
            interval_days=interval,
            repetitions=repetitions,
            next_review_at=next_at,
            updated_at=datetime.now(),
        )
        self.db.log(
            user,
            "grade_card",
            note_id=card["note_id"],
            detail={"card_id": card_id, "grade": grade, "interval_days": interval,
                    "next_review_at": str(next_at)},
        )

        # Memory: a failed card marks the topic as a weak point, passed cards as strong.
        note = next((item for item in self.db.fetch_notes() if item["note_id"] == card["note_id"]), {})
        course = note.get("course", "")
        topic = note.get("topic", "") or card["question"]
        try:
            learning = self.memory.load_learning(user, course)
            weak = [item for item in learning.get("weak", []) if item != topic]
            strong = [item for item in learning.get("strong", []) if item != topic]
            if grade < 3:
                weak.append(topic)
            else:
                strong.append(topic)
            written = self.memory.save_learning(
                user,
                course=course,
                weak=weak[-8:],
                strong=strong[-8:],
                reason=f"{card_id} 评分 {grade}",
            )
            memory_result = {"ok": True, "artifact_id": (written["records"] or [{}])[0].get("artifact_id"),
                             "weak": weak[-8:], "strong": strong[-8:]}
        except PowerContextUnavailable as error:
            memory_result = {"ok": False, "error": str(error)}

        return {
            "card_id": card_id,
            "next_review_at": str(next_at),
            "interval_days": interval,
            "ease": round(ease, 3),
            "memory": memory_result,
        }

    # -- profile ------------------------------------------------------------
    def get_profile(self, user: str) -> Dict[str, Any]:
        return self.memory.snapshot(user)

    def set_profile(self, user: str, profile: Dict[str, Any], reason: str = "用户在界面调整检索偏好") -> Dict[str, Any]:
        written = self.memory.save_profile(user, profile, reason=reason)
        self.db.log(user, "save_profile", detail={"profile": normalise_profile(profile), "reason": reason})
        return {"written": written, "snapshot": self.memory.snapshot(user)}

    def set_focus(self, user: str, focus: str) -> Dict[str, Any]:
        return self.memory.save_focus(user, focus, reason="用户在界面切换聚焦课程")


def _json_payload(text: str) -> Optional[str]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


# -- seeding ----------------------------------------------------------------
def card_id_for(note_id: str, question: str) -> str:
    digest = hashlib.sha256(f"{note_id}:{question}".encode("utf-8")).hexdigest()[:12]
    return f"card_{digest}"


def build_cards(note: Dict[str, Any], rng: Optional[random.Random] = None) -> List[Dict[str, Any]]:
    """Derive follow-up questions from a note (the 'turn reading into review' step)."""
    body = note["body"]
    note_id = note.get("note_id") or note.get("id")
    sentences = [part.strip() for part in re.split(r"[。；;]", body) if len(part.strip()) >= 12]
    rng = rng or random.Random(note_id)
    picked = sentences[:3] or [body[:120]]
    cards = []
    for index, sentence in enumerate(picked):
        question = f"《{note['title']}》里这一段讲的是：{sentence[:60]}…（回忆要点）"
        if index == 1:
            question = f"用自己的话解释《{note['title']}》中的：{sentence[:50]}…"
        cards.append(
            {
                "card_id": card_id_for(note_id, question),
                "note_id": note_id,
                "question": question,
                "answer": sentence,
                "card_kind": "recall" if index == 0 else "explain",
            }
        )
    return cards


def seed(db: DB, settings: Settings = SETTINGS, data_path=None, *, reset: bool = False) -> Dict[str, Any]:
    """Load ``data/seed.json`` into seekdb: notes, embeddings and review cards."""
    from pathlib import Path

    path = data_path or Path(__file__).resolve().parents[2] / "data" / "seed.json"
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    db.ensure_schema()

    if reset:
        with db.cursor() as cur:
            cur.execute("DELETE FROM review_card")
            cur.execute("DELETE FROM note")
            cur.execute("DELETE FROM event_log")

    notes: List[Dict[str, Any]] = []
    for note in payload["notes"]:
        notes.append(note)
    for paper in payload.get("papers", []):
        notes.append(
            {
                "id": "paper-" + re.sub(r"[^a-z0-9]+", "-", paper["title"].lower())[:40].strip("-"),
                "course": paper.get("course", ""),
                "topic": paper.get("topic", ""),
                "source_type": "paper",
                "tags": [paper.get("venue", ""), paper.get("topic", "")] + re.findall(r"[A-Za-z][A-Za-z0-9\-]+", paper["title"])[:3],
                "title": paper["title"],
                "body": f"{paper['authors']}，{paper['venue']}。摘要：{paper['abstract']}",
            }
        )

    texts = [f"{note['title']}。{note['body']}" for note in notes]
    vectors = embed(texts, settings.embedding_provider, settings.embedding_model, settings.embedding_dim)
    from .embedding import vector_literal

    now = datetime.now()
    rng = random.Random(20261104)
    card_count = 0
    for note, vector in zip(notes, vectors):
        db.upsert_note(
            note_id=note["id"],
            source_type=note["source_type"],
            course=note["course"],
            topic=note["topic"],
            tags=[tag for tag in note["tags"] if tag],
            title=note["title"],
            body=note["body"],
            embedding=vector_literal(vector),
            created_at=now,
        )
        for card in build_cards(note, rng):
            due = now + timedelta(days=rng.choice([0, 0, 0, 1, 2, 3]))
            db.insert_card(
                {
                    **card,
                    "ease": 2.5,
                    "interval_days": 0,
                    "repetitions": 0,
                    "next_review_at": due,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            card_count += 1

    db.log(
        "system",
        "seed",
        detail={"notes": len(notes), "cards": card_count, "embedder": texts and "local"},
    )
    return {
        "notes": len(notes),
        "cards": card_count,
        "papers": len(payload.get("papers", [])),
        "embedder": get_embedder(
            settings.embedding_provider, settings.embedding_model, settings.embedding_dim
        ).name,
    }
