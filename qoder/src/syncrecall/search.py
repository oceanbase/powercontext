"""Hybrid retrieval that runs inside seekdb.

Why one statement
-----------------
seekdb speaks MySQL, so the vector branch (``cosine_distance`` + ``APPROXIMATE``
index scan) and the keyword branch (``MATCH ... AGAINST`` with the ``ik`` parser)
are both available. Running them as two queries and merging them in Python would
mean moving every candidate row out of the database. Instead this module builds
**one** SQL statement that:

1. applies the structured condition derived from the user profile kept in
   PowerContext (``course``, ``source_type``, ``tag``) — see :mod:`syncrecall.memory`;
2. runs the full-text branch over the same rows;
3. runs the vector branch over the same rows with ``ORDER BY ... APPROXIMATE``;
4. fuses the two rankings with weighted RRF inside the database;
5. returns one ranked list together with the two individual scores for display.

The generated statement is returned to the caller, so the UI can show exactly what
was executed, and ``tests/test_search.py`` pins the shape down.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from .embedding import vector_literal
from .store import DB

RRF_K = 60
CANDIDATE_LIMIT = 60


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fulltext_query(keywords: Sequence[str]) -> str:
    """``ik`` segments Chinese text, so keywords are joined with spaces.

    Tokens are kept verbatim (``Raft``, ``MVCC``, ``B+树`` stay whole) and any
    character that could be read as FTS syntax is dropped.
    """
    seen: List[str] = []
    for keyword in keywords:
        token = str(keyword).replace("'", "").replace('"', "").strip()
        if token and token not in seen:
            seen.append(token)
    return " ".join(seen)


_FUSED_EXPRESSION = (
    f"ROUND(%s / ({RRF_K} + COALESCE(fts_rank, {CANDIDATE_LIMIT + 1})), 6)"
    f" + ROUND(%s / ({RRF_K} + COALESCE(vec_rank, {CANDIDATE_LIMIT + 1})), 6)"
)


def build_hybrid_sql(
    *,
    text: str,
    vector: Sequence[float],
    filters: Optional[Dict[str, Any]] = None,
    keyword_weight: float = 0.6,
    vector_weight: float = 0.4,
    limit: int = 8,
) -> Dict[str, Any]:
    """Build one self-contained hybrid statement.

    ``filters`` understands the keys the UI exposes:

    ``courses``      list[str] — restrict to these courses
    ``source_types`` list[str] — note / paper / chat / meeting
    ``tag``          str       — one tag, matched on word boundaries
    """
    filters = filters or {}
    where: List[str] = []
    filter_params: List[Any] = []

    courses = [str(item) for item in (filters.get("courses") or []) if str(item).strip()]
    if courses:
        where.append("course IN (" + ", ".join(["%s"] * len(courses)) + ")")
        filter_params.extend(courses)

    source_types = [str(item) for item in (filters.get("source_types") or []) if str(item).strip()]
    if source_types:
        where.append("source_type IN (" + ", ".join(["%s"] * len(source_types)) + ")")
        filter_params.extend(source_types)

    tag = str(filters.get("tag") or "").strip()
    if tag:
        where.append("CONCAT(',', tags, ',') LIKE %s")
        filter_params.append(f"%,{_escape_like(tag)},%")

    condition = ("WHERE " + " AND ".join(where)) if where else ""
    vector_literal_text = vector_literal(list(vector))
    text_literal = "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"
    distance = f"cosine_distance(embedding, '{vector_literal_text}')"

    sql = (
        "SELECT note_id, title, course, topic, source_type, tags,\n"
        "       SUBSTRING(body, 1, 420) AS excerpt, fts_score, vec_score,\n"
        f"       {_FUSED_EXPRESSION} AS fused_score\n"
        "FROM (\n"
        "  SELECT note_id, title, course, topic, source_type, tags, body, fts_score, vec_score,\n"
        "         RANK() OVER (PARTITION BY fts_score IS NULL ORDER BY fts_score DESC) AS fts_rank,\n"
        "         RANK() OVER (PARTITION BY vec_score IS NULL ORDER BY vec_score DESC) AS vec_rank\n"
        "  FROM (\n"
        "    SELECT note_id, title, course, topic, source_type, tags, body,\n"
        f"           MATCH(title, body) AGAINST ({text_literal} IN NATURAL LANGUAGE MODE) AS fts_score,\n"
        f"           (1 - {distance}) AS vec_score\n"
        "    FROM note\n"
        f"    {condition}\n"
        f"    ORDER BY {distance} APPROXIMATE\n"
        f"    LIMIT {CANDIDATE_LIMIT}\n"
        "  ) candidates\n"
        ") ranked\n"
        "WHERE (fts_score IS NOT NULL AND fts_score > 0) OR vec_score IS NOT NULL\n"
        "ORDER BY fused_score DESC, title\n"
        f"LIMIT {int(limit)}"
    )
    params = [float(keyword_weight), float(vector_weight)] + filter_params
    return {"sql": sql, "params": params, "text": text}


def hybrid_search(
    db: DB,
    *,
    query: str,
    keywords: Sequence[str],
    vector: Sequence[float],
    filters: Optional[Dict[str, Any]] = None,
    keyword_weight: float = 0.6,
    vector_weight: float = 0.4,
    limit: int = 8,
    scope_id: str = "",
    actor: str = "app",
) -> Dict[str, Any]:
    """Run the hybrid statement and return hits plus the audit record."""
    text = fulltext_query(keywords) or fulltext_query([query])
    built = build_hybrid_sql(
        text=text,
        vector=vector,
        filters=filters,
        keyword_weight=keyword_weight,
        vector_weight=vector_weight,
        limit=limit,
    )
    started = time.perf_counter()
    with db.cursor() as cur:
        cur.execute(built["sql"], built["params"])
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    hits = []
    for row in rows:
        fts_score = row["fts_score"]
        vec_score = row["vec_score"]
        hits.append(
            {
                "note_id": row["note_id"],
                "title": row["title"],
                "course": row["course"],
                "topic": row["topic"],
                "source_type": row["source_type"],
                "tags": [tag for tag in str(row["tags"] or "").split(",") if tag],
                "excerpt": row["excerpt"],
                "fts_score": None if fts_score is None else round(float(fts_score), 6),
                "vec_score": None if vec_score is None else round(float(vec_score), 6),
                "score": round(float(row["fused_score"]), 6),
            }
        )

    db.log(
        actor,
        "hybrid_search",
        scope_id=scope_id,
        detail={
            "query": query,
            "keywords": text,
            "filters": filters or {},
            "weights": [keyword_weight, vector_weight],
            "hits": [hit["note_id"] for hit in hits],
        },
        elapsed_ms=elapsed_ms,
    )
    return {
        "hits": hits,
        "sql": built["sql"],
        "params": built["params"],
        "fulltext_query": text,
        "elapsed_ms": elapsed_ms,
        "engine": {"fts_parser": "ik", "fusion": "weighted RRF", "rrf_k": RRF_K},
    }
