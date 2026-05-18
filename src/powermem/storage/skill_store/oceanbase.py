"""OceanBase implementation of SkillStore."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from .base import SkillStoreBase

logger = logging.getLogger(__name__)


def _index_exists(engine, table_name: str, index_name: str) -> bool:
    """Return True if *index_name* exists on *table_name*.

    Uses an independent connection so the probe does not trigger SQLAlchemy 2.x
    autobegin on a connection that the caller still needs for its own transactions.
    """
    with engine.connect() as probe:
        row = probe.execute(text(
            "SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i "
            "LIMIT 1"
        ), {"t": table_name, "i": index_name}).first()
        return row is not None


class OceanBaseSkillStore(SkillStoreBase):
    """Skill storage backed by OceanBase with dual-vector + dual-fulltext search."""

    def __init__(
        self,
        engine,
        table_name: str = "skills",
        embedding_dims: int = 1536,
        fulltext_parser: str = "ngram",
        index_type: str = "hnsw",
    ):
        self.engine = engine
        self.table_name = table_name
        self.embedding_dims = embedding_dims
        self.fulltext_parser = fulltext_parser
        self.index_type = index_type
        self.create_table()

    def create_table(self) -> None:
        with self.engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS `{self.table_name}` (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(128),
                        agent_id VARCHAR(128),
                        title VARCHAR(256) NOT NULL,
                        title_embedding VECTOR({self.embedding_dims}),
                        description TEXT NOT NULL,
                        description_embedding VECTOR({self.embedding_dims}),
                        tags JSON,
                        procedure_data JSON NOT NULL,
                        status VARCHAR(32) DEFAULT 'draft',
                        positive_count INT DEFAULT 0,
                        negative_count INT DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) DEFAULT CHARSET=utf8mb4
                """))

            for idx_sql in [
                f"CREATE INDEX idx_{self.table_name}_user ON `{self.table_name}` (user_id, agent_id)",
                f"CREATE INDEX idx_{self.table_name}_status ON `{self.table_name}` (status)",
                f"CREATE FULLTEXT INDEX idx_{self.table_name}_title_ft ON `{self.table_name}` (title) WITH PARSER {self.fulltext_parser}",
                f"CREATE FULLTEXT INDEX idx_{self.table_name}_desc_ft ON `{self.table_name}` (description) WITH PARSER {self.fulltext_parser}",
            ]:
                try:
                    with conn.begin():
                        conn.execute(text(idx_sql))
                except Exception:
                    pass

            # Build index-type-specific WITH params
            idx_type_lower = self.index_type.lower()
            if idx_type_lower.startswith("ivf"):
                vec_with = f"distance=cosine, type={self.index_type}, nlist=128"
            else:
                vec_with = f"distance=cosine, type={self.index_type}, m=16, ef_construction=200"
            for idx_name, col in [
                (f"idx_{self.table_name}_title_vec", "title_embedding"),
                (f"idx_{self.table_name}_desc_vec", "description_embedding"),
            ]:
                if _index_exists(self.engine, self.table_name, idx_name):
                    continue
                vidx_sql = (
                    f"CREATE VECTOR INDEX {idx_name} ON `{self.table_name}` ({col}) "
                    f"WITH ({vec_with})"
                )
                try:
                    with self.engine.begin() as conn2:
                        conn2.execute(text(vidx_sql))
                except Exception as e:
                    msg = str(e).lower()
                    if "duplicate" in msg or "exists" in msg or "not supported" in msg or "1235" in msg:
                        logger.debug("SkillStore vector index already exists or not supported: %s", self.table_name)
                    else:
                        logger.warning("Vector index creation failed: %s", e)

        logger.info("SkillStore table '%s' initialized", self.table_name)

    def add(
        self,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        procedure_data: Optional[Dict[str, Any]] = None,
        title_embedding: Optional[List[float]] = None,
        description_embedding: Optional[List[float]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "title": title,
            "description": description,
            "tags": json.dumps(tags) if tags else None,
            "procedure_data": json.dumps(procedure_data, ensure_ascii=False) if procedure_data else "{}",
            "user_id": user_id,
            "agent_id": agent_id,
        }

        cols = ["title", "description", "tags", "procedure_data", "user_id", "agent_id"]
        vals = [":title", ":description", ":tags", ":procedure_data", ":user_id", ":agent_id"]

        if title_embedding:
            params["title_embedding"] = str(title_embedding)
            cols.append("title_embedding")
            vals.append(":title_embedding")
        if description_embedding:
            params["description_embedding"] = str(description_embedding)
            cols.append("description_embedding")
            vals.append(":description_embedding")

        sql = text(f"INSERT INTO `{self.table_name}` ({', '.join(cols)}) VALUES ({', '.join(vals)})")

        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(sql, params)
                skill_id = result.lastrowid
                return {"id": skill_id, "title": title}

    def update(
        self,
        skill_id: int,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        procedure_data: Optional[Dict[str, Any]] = None,
        title_embedding: Optional[List[float]] = None,
        description_embedding: Optional[List[float]] = None,
    ) -> bool:
        set_clauses = [
            "title = :title", "description = :description",
            "tags = :tags", "procedure_data = :procedure_data",
            "updated_at = NOW()",
        ]
        params: Dict[str, Any] = {
            "id": skill_id,
            "title": title,
            "description": description,
            "tags": json.dumps(tags) if tags else None,
            "procedure_data": json.dumps(procedure_data, ensure_ascii=False) if procedure_data else "{}",
        }
        if title_embedding:
            params["title_embedding"] = str(title_embedding)
            set_clauses.append("title_embedding = :title_embedding")
        if description_embedding:
            params["description_embedding"] = str(description_embedding)
            set_clauses.append("description_embedding = :description_embedding")

        sql = text(f"UPDATE `{self.table_name}` SET {', '.join(set_clauses)} WHERE id = :id")
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(sql, params)
                return result.rowcount > 0

    def get(self, skill_id: int) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM `{self.table_name}` WHERE id = :id"),
                {"id": skill_id},
            ).mappings().fetchone()
            return self._row_to_dict(row) if row else None

    def update_feedback(self, skill_id: int, positive: bool) -> bool:
        """Increment positive_count or negative_count."""
        col = "positive_count" if positive else "negative_count"
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(
                    text(f"UPDATE `{self.table_name}` SET {col} = {col} + 1 WHERE id = :id"),
                    {"id": skill_id},
                )
                return result.rowcount > 0

    def update_status(self, skill_id: int, status: str) -> bool:
        """Update the status field of a skill."""
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(
                    text(f"UPDATE `{self.table_name}` SET status = :status, updated_at = NOW() WHERE id = :id"),
                    {"status": status, "id": skill_id},
                )
                return result.rowcount > 0

    def delete(self, skill_id: int) -> bool:
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(
                    text(f"DELETE FROM `{self.table_name}` WHERE id = :id"),
                    {"id": skill_id},
                )
                return result.rowcount > 0

    def search(
        self,
        query_embedding: Optional[List[float]] = None,
        query_text: Optional[str] = None,
        limit: int = 10,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where, where_params = self._build_where(user_id, agent_id, status_filter)
        candidate_limit = min(limit * 3, 50)

        futures = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            if query_embedding:
                futures["title_vec"] = pool.submit(
                    self._vector_search, "title_embedding", query_embedding, where, where_params, candidate_limit,
                )
                futures["desc_vec"] = pool.submit(
                    self._vector_search, "description_embedding", query_embedding, where, where_params, candidate_limit,
                )
            if query_text:
                futures["title_ft"] = pool.submit(
                    self._fulltext_search, "title", query_text, where, where_params, candidate_limit,
                )
                futures["desc_ft"] = pool.submit(
                    self._fulltext_search, "description", query_text, where, where_params, candidate_limit,
                )

        all_results = {}
        for key, future in futures.items():
            try:
                all_results[key] = future.result()
            except Exception as e:
                logger.warning("Skill search %s failed: %s", key, e)
                all_results[key] = []

        return self._rrf_fusion(all_results, limit)

    def _build_where(self, user_id, agent_id, status_filter) -> tuple:
        """Returns (where_clause, params_dict) with parameterized values."""
        conditions = ["status != 'deprecated'"]
        params = {}
        if user_id:
            conditions.append("user_id = :w_user_id")
            params["w_user_id"] = user_id
        if agent_id:
            conditions.append("agent_id = :w_agent_id")
            params["w_agent_id"] = agent_id
        if status_filter:
            conditions.append("status = :w_status")
            params["w_status"] = status_filter
        return " AND ".join(conditions), params

    def _vector_search(self, col, embedding, where, where_params, limit):
        sql = text(f"""
            SELECT *, cosine_distance({col}, :emb) AS distance
            FROM `{self.table_name}`
            WHERE {where}
            ORDER BY cosine_distance({col}, :emb)
            LIMIT :limit
        """)
        params = {**where_params, "emb": str(embedding), "limit": limit}
        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().fetchall()
            results = []
            for r in rows:
                d = self._row_to_dict(r)
                d["score"] = 1.0 - float(r.get("distance", 0)) / 2.0
                results.append(d)
            return results

    def _fulltext_search(self, col, query, where, where_params, limit):
        sql = text(f"""
            SELECT *, MATCH({col}) AGAINST(:query IN NATURAL LANGUAGE MODE) AS score
            FROM `{self.table_name}`
            WHERE {where} AND MATCH({col}) AGAINST(:query IN NATURAL LANGUAGE MODE)
            ORDER BY score DESC
            LIMIT :limit
        """)
        params = {**where_params, "query": query, "limit": limit}
        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().fetchall()
            results = []
            for r in rows:
                d = self._row_to_dict(r)
                d["score"] = float(r.get("score", 0))
                results.append(d)
            return results

    @staticmethod
    def _rrf_fusion(all_results, limit, k=60):
        scores = {}
        docs = {}
        for results in all_results.values():
            for rank, doc in enumerate(results):
                doc_id = doc["id"]
                scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
                if doc_id not in docs:
                    docs[doc_id] = doc
        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]
        return [{**docs[did], "score": scores[did]} for did in sorted_ids]

    @staticmethod
    def _row_to_dict(row):
        d = dict(row)
        d.pop("title_embedding", None)
        d.pop("description_embedding", None)
        d.pop("distance", None)
        d.pop("score", None)
        for field in ("tags", "procedure_data"):
            val = d.get(field)
            if isinstance(val, str):
                try:
                    d[field] = json.loads(val)
                except (ValueError, TypeError):
                    pass
        for field in ("created_at", "updated_at"):
            val = d.get(field)
            if val and hasattr(val, "isoformat"):
                d[field] = val.isoformat()
        return d
