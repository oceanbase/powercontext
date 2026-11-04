"""seekdb schema, connection and the storage primitives.

Role of seekdb in this project
------------------------------
seekdb is the application's only persistent store:

* ``note``       — sources: course notes, papers, chat excerpts, meeting minutes,
                   each chunked, embedded and indexed for **hybrid search**
                   (full-text ``MATCH ... AGAINST`` + vector ``cosine_distance``)
                   inside a single SQL statement;
* ``review_card``— progress: every follow-up item derived from a note, with its
                   ``next_review_at`` computed by SM-2, which is how the app turns
                   "remember this for me" into "ask me again when it is due";
* ``event_log``  — an append-only audit trail, so the reviewer can prove that a
                   retrieval / a memory write really happened.

The full-text index uses seekdb's ``ik`` parser (Chinese word segmentation); the
vector index is HNSW over a ``VECTOR(n)`` column.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

import pymysql

from .config import SETTINGS

SCHEMA: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS note (
      note_id      VARCHAR(64)  NOT NULL PRIMARY KEY,
      source_type  VARCHAR(32)  NOT NULL,
      course       VARCHAR(128) NOT NULL DEFAULT '',
      topic        VARCHAR(128) NOT NULL DEFAULT '',
      tags         VARCHAR(512) NOT NULL DEFAULT '',
      title        VARCHAR(512) NOT NULL,
      body         TEXT         NOT NULL,
      embedding    VECTOR(384)  NOT NULL,
      created_at   DATETIME(6)  NOT NULL,
      FULLTEXT INDEX ft_note (title, body) WITH PARSER ik,
      VECTOR INDEX vidx_note (embedding) WITH (distance=cosine, type=hnsw, lib=vsag)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS review_card (
      card_id        VARCHAR(64)  NOT NULL PRIMARY KEY,
      note_id        VARCHAR(64)  NOT NULL,
      question       VARCHAR(512) NOT NULL,
      answer         TEXT         NOT NULL,
      card_kind      VARCHAR(16)  NOT NULL DEFAULT 'recall',
      ease           DOUBLE       NOT NULL DEFAULT 2.5,
      interval_days  INT          NOT NULL DEFAULT 0,
      repetitions    INT          NOT NULL DEFAULT 0,
      next_review_at DATETIME(6)  NOT NULL,
      created_at     DATETIME(6)  NOT NULL,
      updated_at     DATETIME(6)  NOT NULL,
      KEY idx_card_due (next_review_at),
      KEY idx_card_note (note_id)
    ) DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS event_log (
      event_id   BIGINT      NOT NULL PRIMARY KEY AUTO_INCREMENT,
      created_at DATETIME(6) NOT NULL,
      actor      VARCHAR(64) NOT NULL,
      action     VARCHAR(64) NOT NULL,
      scope_id   VARCHAR(128) NOT NULL DEFAULT '',
      note_id    VARCHAR(64) NOT NULL DEFAULT '',
      detail     MEDIUMTEXT  NOT NULL,
      elapsed_ms INT         NOT NULL DEFAULT 0
    ) DEFAULT CHARSET = utf8mb4
    """,
)


class DB:
    """Small helper around pymysql; one connection per operation."""

    def __init__(self, settings=SETTINGS, *, dim: Optional[int] = None) -> None:
        self.settings = settings
        self.dim = dim or settings.embedding_dim
        self.database = settings.db_name

    # -- connection ---------------------------------------------------------
    def _connect(self, with_db: bool = True) -> pymysql.connections.Connection:
        kwargs = dict(self.settings.db_kwargs)
        if with_db:
            kwargs["database"] = self.database
        return pymysql.connect(
            **kwargs,
            autocommit=True,
            connect_timeout=10,
            read_timeout=60,
            write_timeout=60,
        )

    @contextmanager
    def cursor(self) -> Iterator[pymysql.cursors.Cursor]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                yield cur
        finally:
            conn.close()

    # -- schema -------------------------------------------------------------
    def version(self) -> str:
        conn = self._connect(with_db=False)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                return str(cur.fetchone()[0])
        finally:
            conn.close()

    def ensure_database(self) -> None:
        conn = self._connect(with_db=False)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARACTER SET utf8mb4" % self.database
                )
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        self.ensure_database()
        with self.cursor() as cur:
            for statement in SCHEMA:
                sql = statement.strip()
                if self.dim != 384:
                    sql = sql.replace("VECTOR(384)", f"VECTOR({self.dim})")
                cur.execute(sql)

    def table_count(self, table: str) -> int:
        with self.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{table}`")
            return int(cur.fetchone()[0])

    # -- writes -------------------------------------------------------------
    def upsert_note(
        self,
        *,
        note_id: str,
        source_type: str,
        course: str,
        topic: str,
        tags: Iterable[str],
        title: str,
        body: str,
        embedding: str,
        created_at: datetime,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO note(note_id, source_type, course, topic, tags, title, body, embedding, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE source_type = VALUES(source_type), course = VALUES(course),
                  topic = VALUES(topic), tags = VALUES(tags), title = VALUES(title),
                  body = VALUES(body), embedding = VALUES(embedding)
                """,
                (
                    note_id,
                    source_type,
                    course,
                    topic,
                    ",".join(tags),
                    title,
                    body,
                    embedding,
                    created_at,
                ),
            )

    def insert_card(self, row: Dict[str, Any]) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO review_card(card_id, note_id, question, answer, card_kind, ease,
                  interval_days, repetitions, next_review_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE question = VALUES(question), answer = VALUES(answer)
                """,
                (
                    row["card_id"],
                    row["note_id"],
                    row["question"],
                    row["answer"],
                    row.get("card_kind", "recall"),
                    row.get("ease", 2.5),
                    row.get("interval_days", 0),
                    row.get("repetitions", 0),
                    row["next_review_at"],
                    row.get("created_at", datetime.now()),
                    row.get("updated_at", datetime.now()),
                ),
            )

    def update_card(self, card_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"`{name}` = %s" for name in fields)
        values = list(fields.values()) + [card_id]
        with self.cursor() as cur:
            cur.execute(f"UPDATE review_card SET {columns} WHERE card_id = %s", values)

    def log(self, actor: str, action: str, *, scope_id: str = "", note_id: str = "",
            detail: Optional[Dict[str, Any]] = None, elapsed_ms: int = 0) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_log(created_at, actor, action, scope_id, note_id, detail, elapsed_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    datetime.now(),
                    actor,
                    action,
                    scope_id,
                    note_id,
                    json.dumps(detail or {}, ensure_ascii=False),
                    int(elapsed_ms),
                ),
            )

    # -- reads --------------------------------------------------------------
    def fetch_notes(self) -> List[Dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT note_id, source_type, course, topic, tags, title, body, created_at FROM note ORDER BY note_id"
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def fetch_cards(self, *, note_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        sql = (
            "SELECT card_id, note_id, question, answer, card_kind, ease, interval_days, repetitions,"
            " next_review_at, created_at FROM review_card"
        )
        params: List[Any] = []
        if note_ids:
            placeholders = ", ".join(["%s"] * len(note_ids))
            sql += f" WHERE note_id IN ({placeholders})"
            params.extend(note_ids)
        sql += " ORDER BY next_review_at"
        with self.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def fetch_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, created_at, actor, action, scope_id, note_id, detail, elapsed_ms
                FROM event_log ORDER BY event_id DESC LIMIT %s
                """,
                (limit,),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def connect_probe(settings=SETTINGS) -> Optional[str]:
    """Return the seekdb version, or ``None`` when it is unreachable."""
    try:
        conn = pymysql.connect(**settings.db_kwargs, connect_timeout=3)
    except Exception:  # noqa: BLE001 - the caller only needs reachability
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            return str(cur.fetchone()[0])
    finally:
        conn.close()
