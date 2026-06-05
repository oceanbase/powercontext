"""OceanBase implementation of SourceStore (fact-source linking)."""

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from .base import SourceStoreBase

logger = logging.getLogger(__name__)


class OceanBaseSourceStore(SourceStoreBase):
    """Source storage backed by OceanBase.

    Creates three tables:
    - ``{table_name}``                     -- source records
    - ``{table_name}_memory_links``        -- many-to-many: source <-> memory
    - ``{table_name}_skill_links``         -- many-to-many: source <-> skill

    Migration: an existing ``{table_name}_links`` table from the previous
    single-target schema is renamed to ``{table_name}_memory_links`` on init
    so old deployments transparently upgrade.
    """

    def __init__(
        self,
        engine,
        table_name: str = "sources",
    ):
        self.engine = engine
        self.table_name = table_name
        self.memory_link_table = f"{table_name}_memory_links"
        self.skill_link_table = f"{table_name}_skill_links"
        self.create_table()

    # ------------------------------------------------------------------ #
    # DDL + migration
    # ------------------------------------------------------------------ #

    def create_table(self) -> None:
        with self.engine.connect() as conn:
            with conn.begin():
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS `{self.table_name}` (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        source_type VARCHAR(64) NOT NULL DEFAULT 'conversation',
                        content LONGTEXT NOT NULL,
                        metadata JSON,
                        user_id VARCHAR(128),
                        agent_id VARCHAR(128),
                        run_id VARCHAR(128),
                        actor_id VARCHAR(128),
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    ) DEFAULT CHARSET=utf8mb4
                """))

            # Migration: rename legacy single-target link table to the new
            # _memory_links name, but only if old exists and new does not.
            self._maybe_migrate_legacy_link_table(conn)

            with conn.begin():
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS `{self.memory_link_table}` (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        source_id BIGINT NOT NULL,
                        memory_id BIGINT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_source_memory (source_id, memory_id)
                    ) DEFAULT CHARSET=utf8mb4
                """))
                conn.execute(text(f"""
                    CREATE TABLE IF NOT EXISTS `{self.skill_link_table}` (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        source_id BIGINT NOT NULL,
                        skill_id BIGINT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_source_skill (source_id, skill_id)
                    ) DEFAULT CHARSET=utf8mb4
                """))

            # Indexes -- "duplicate key" is expected on re-init; log everything
            # else so DDL problems (connectivity, permissions) surface instead
            # of silently disappearing.
            for idx_sql in [
                f"CREATE INDEX idx_{self.table_name}_user ON `{self.table_name}` (user_id, agent_id, run_id)",
                f"CREATE INDEX idx_{self.table_name}_type ON `{self.table_name}` (source_type)",
                f"CREATE INDEX idx_{self.memory_link_table}_mem ON `{self.memory_link_table}` (memory_id)",
                f"CREATE INDEX idx_{self.skill_link_table}_skill ON `{self.skill_link_table}` (skill_id)",
            ]:
                try:
                    with conn.begin():
                        conn.execute(text(idx_sql))
                except Exception as e:
                    msg = str(e).lower()
                    if "duplicate" in msg or "exists" in msg:
                        logger.debug("SourceStore index already exists: %s", idx_sql)
                    else:
                        logger.warning("SourceStore index creation failed (%s): %s", idx_sql, e)

        logger.info(
            "SourceStore tables initialized: %s + (%s, %s)",
            self.table_name,
            self.memory_link_table,
            self.skill_link_table,
        )

    def _maybe_migrate_legacy_link_table(self, conn) -> None:
        """Rename ``{table_name}_links`` -> ``{table_name}_memory_links`` if needed.

        Old deployments created a single ``{table_name}_links`` table. The
        new layout splits the link tables by target type. This rename is the
        only data-bearing migration step; if the target name already exists
        we assume the rename has already happened (or we are in a fresh
        install) and skip silently.
        """
        legacy = f"{self.table_name}_links"
        if not self._table_exists(conn, legacy):
            return
        if self._table_exists(conn, self.memory_link_table):
            # Both tables exist -- a previous partial migration or a manual
            # CREATE. Don't clobber data; just warn and let the operator
            # decide.
            logger.warning(
                "SourceStore migration: both '%s' (legacy) and '%s' exist; "
                "skipping rename. Resolve manually if data needs merging.",
                legacy, self.memory_link_table,
            )
            return
        try:
            with conn.begin():
                conn.execute(text(
                    f"RENAME TABLE `{legacy}` TO `{self.memory_link_table}`"
                ))
            logger.info(
                "SourceStore migration: renamed '%s' -> '%s'",
                legacy, self.memory_link_table,
            )
        except Exception as e:
            # RENAME can fail for two distinct reasons; the recovery action is
            # different for each, so spell both out for the operator:
            #
            # 1. Real DDL error (permission denied, table locked, etc.): the
            #    legacy table is still in place, the new table will be created
            #    empty by the CREATE IF NOT EXISTS that follows, and legacy
            #    data needs manual rescue from `{legacy}`.
            # 2. Concurrent init race: another process already RENAMEd the
            #    same legacy table; ours fails because `{legacy}` no longer
            #    exists. Data is intact under `{memory_link_table}` and no
            #    operator action is required.
            logger.warning(
                "SourceStore migration: failed to rename '%s' -> '%s': %s. "
                "If another instance already performed this migration, data "
                "is intact in '%s' and no action is required. Otherwise, "
                "legacy data may still be in '%s' and a new empty '%s' will "
                "be created by the subsequent CREATE IF NOT EXISTS.",
                legacy, self.memory_link_table, e,
                self.memory_link_table, legacy, self.memory_link_table,
            )

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        """Return True if a table with ``table_name`` exists in the current schema.

        The probe is wrapped in its own ``with conn.begin():`` so it commits
        cleanly and does not leave the connection in a half-open autobegun
        transaction. SQLAlchemy 2.x autobegins on bare ``conn.execute()``
        calls; without an explicit transaction the next ``with conn.begin():``
        in the caller raises ``InvalidRequestError`` ("connection has already
        initialized a Transaction"). See ``create_table`` for the surrounding
        flow that requires this invariant.
        """
        try:
            with conn.begin():
                row = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() AND table_name = :name "
                        "LIMIT 1"
                    ),
                    {"name": table_name},
                ).fetchone()
                return row is not None
        except Exception as e:
            logger.warning("SourceStore: _table_exists probe failed for %s: %s", table_name, e)
            return False

    # ------------------------------------------------------------------ #
    # Source CRUD
    # ------------------------------------------------------------------ #

    def create_source(
        self,
        source_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "source_type": source_type,
            "content": content,
            "metadata": json.dumps(metadata) if metadata else None,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "actor_id": actor_id,
        }
        sql = text(f"""
            INSERT INTO `{self.table_name}`
                (source_type, content, metadata, user_id, agent_id, run_id, actor_id)
            VALUES
                (:source_type, :content, :metadata, :user_id, :agent_id, :run_id, :actor_id)
        """)
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(sql, params)
                source_id = result.lastrowid
                return {
                    "id": source_id,
                    "source_type": source_type,
                    "content": content,
                    "metadata": metadata,
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "run_id": run_id,
                    "actor_id": actor_id,
                }

    def get_source(self, source_id: int) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM `{self.table_name}` WHERE id = :id"),
                {"id": source_id},
            ).mappings().fetchone()
            return self._row_to_dict(row) if row else None

    def delete_source(self, source_id: int) -> bool:
        with self.engine.connect() as conn:
            with conn.begin():
                # Delete links first, then the source row. We sweep both
                # link tables so a delete fully detaches the source.
                for tbl in (
                    self.memory_link_table,
                    self.skill_link_table,
                ):
                    conn.execute(
                        text(f"DELETE FROM `{tbl}` WHERE source_id = :sid"),
                        {"sid": source_id},
                    )
                result = conn.execute(
                    text(f"DELETE FROM `{self.table_name}` WHERE id = :id"),
                    {"id": source_id},
                )
                return result.rowcount > 0

    # ------------------------------------------------------------------ #
    # Memory linking
    # ------------------------------------------------------------------ #

    def link_memory(self, source_id: int, memory_id: int) -> bool:
        return self._link(self.memory_link_table, "memory_id", source_id, memory_id)

    def unlink_memory(self, source_id: int, memory_id: int) -> bool:
        return self._unlink(self.memory_link_table, "memory_id", source_id, memory_id)

    def get_sources_for_memory(self, memory_id: int) -> List[Dict[str, Any]]:
        return self._get_sources_for_target(self.memory_link_table, "memory_id", memory_id)

    # ------------------------------------------------------------------ #
    # Skill linking
    # ------------------------------------------------------------------ #

    def link_skill(self, source_id: int, skill_id: int) -> bool:
        return self._link(self.skill_link_table, "skill_id", source_id, skill_id)

    def unlink_skill(self, source_id: int, skill_id: int) -> bool:
        return self._unlink(self.skill_link_table, "skill_id", source_id, skill_id)

    def get_sources_for_skill(self, skill_id: int) -> List[Dict[str, Any]]:
        return self._get_sources_for_target(self.skill_link_table, "skill_id", skill_id)

    # ------------------------------------------------------------------ #
    # Reverse queries (source -> targets)
    # ------------------------------------------------------------------ #

    def get_memories_for_source(self, source_id: int) -> List[int]:
        return self._get_targets_for_source(self.memory_link_table, "memory_id", source_id)

    def get_skills_for_source(self, source_id: int) -> List[int]:
        return self._get_targets_for_source(self.skill_link_table, "skill_id", source_id)

    # ------------------------------------------------------------------ #
    # Generic link / unlink / query implementation
    # ------------------------------------------------------------------ #

    def _link(self, link_table: str, target_col: str, source_id: int, target_id: int) -> bool:
        # ``ON DUPLICATE KEY UPDATE created_at = created_at`` is a NOP that
        # lets us reuse ``rowcount`` to distinguish insert vs. duplicate:
        # MySQL/OceanBase returns 1 for a fresh insert and 0 when the unique
        # key already exists.
        sql = text(f"""
            INSERT INTO `{link_table}` (source_id, {target_col}, created_at)
            VALUES (:source_id, :target_id, NOW())
            ON DUPLICATE KEY UPDATE created_at = created_at
        """)
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(sql, {"source_id": source_id, "target_id": target_id})
                if result.rowcount == 0:
                    logger.info(
                        "link to %s: link already exists (source_id=%s, %s=%s)",
                        link_table, source_id, target_col, target_id,
                    )
                    return False
                return True

    def _unlink(self, link_table: str, target_col: str, source_id: int, target_id: int) -> bool:
        with self.engine.connect() as conn:
            with conn.begin():
                result = conn.execute(
                    text(
                        f"DELETE FROM `{link_table}` "
                        f"WHERE source_id = :sid AND {target_col} = :tid"
                    ),
                    {"sid": source_id, "tid": target_id},
                )
                return result.rowcount > 0

    def _get_sources_for_target(self, link_table: str, target_col: str, target_id: int) -> List[Dict[str, Any]]:
        sql = text(f"""
            SELECT s.* FROM `{self.table_name}` s
            JOIN `{link_table}` l ON s.id = l.source_id
            WHERE l.{target_col} = :target_id
            ORDER BY s.created_at DESC
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"target_id": target_id}).mappings().fetchall()
            return [self._row_to_dict(r) for r in rows]

    def _get_targets_for_source(self, link_table: str, target_col: str, source_id: int) -> List[int]:
        sql = text(
            f"SELECT {target_col} FROM `{link_table}` "
            f"WHERE source_id = :sid ORDER BY created_at ASC"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"sid": source_id}).mappings().fetchall()
            return [int(r[target_col]) for r in rows]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        # Parse JSON metadata
        val = d.get("metadata")
        if isinstance(val, str):
            try:
                d["metadata"] = json.loads(val)
            except (ValueError, TypeError):
                d["metadata"] = None
        # Stringify datetimes
        for field in ("created_at",):
            val = d.get(field)
            if val and hasattr(val, "isoformat"):
                d[field] = val.isoformat()
        return d
