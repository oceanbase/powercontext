"""Unit tests for SourceStore (base contract + OceanBase mock)."""

import pytest
from unittest.mock import MagicMock

# Bind real sqlalchemy symbols AND pre-load the sqlite dialect at module
# import time. tests/unit/test_oceanbase.py replaces
# ``sys.modules['sqlalchemy']`` (and several submodules) with MagicMocks for
# its own legacy dependency mocking; once that runs, any subsequent
# ``create_engine("sqlite:///...")`` blows up because the dialect lookup hits
# the mocked sqlalchemy.dialects module. Pre-binding here -- and forcing the
# sqlite dialect import while sqlalchemy is still the real module -- keeps
# the Round 3 regression tests reliable regardless of test execution order.
from sqlalchemy import create_engine as _real_create_engine
from sqlalchemy import text as _real_text
import sqlalchemy.dialects.sqlite  # noqa: F401 -- pre-cache real sqlite dialect

# Build a real sqlite engine NOW (will be used in regression tests below).
# Doing this at import time means the engine factory and dialect are fully
# resolved against the real sqlalchemy before any other test pollutes it.
_REAL_SQLITE_AVAILABLE = True
try:
    _probe_engine = _real_create_engine("sqlite:///:memory:")
    _probe_engine.dispose()
except Exception:  # pragma: no cover -- environment without sqlite
    _REAL_SQLITE_AVAILABLE = False

from powermem.storage.source_store.base import SourceStoreBase
from powermem.storage.source_store.oceanbase import OceanBaseSourceStore

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_mock_engine():
    """Build a MagicMock that behaves like a SQLAlchemy engine."""
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    conn.begin.return_value.__enter__ = MagicMock()
    conn.begin.return_value.__exit__ = MagicMock(return_value=False)
    return engine, conn


# --------------------------------------------------------------------------- #
# Base contract
# --------------------------------------------------------------------------- #


class TestSourceStoreBase:
    """Verify SourceStoreBase is abstract and declares required methods."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            SourceStoreBase()

    def test_required_methods(self):
        expected = {
            # source CRUD
            "create_table", "create_source", "get_source", "delete_source",
            # memory linking
            "link_memory", "unlink_memory", "get_sources_for_memory",
            # skill linking
            "link_skill", "unlink_skill", "get_sources_for_skill",
            # reverse queries
            "get_memories_for_source", "get_skills_for_source",
        }
        actual = {
            name for name in dir(SourceStoreBase)
            if not name.startswith("_") and callable(getattr(SourceStoreBase, name, None))
        }
        assert expected.issubset(actual)


# --------------------------------------------------------------------------- #
# OceanBase implementation
# --------------------------------------------------------------------------- #


class TestOceanBaseSourceStore:
    """Test OceanBaseSourceStore with a mocked engine."""

    def test_create_table_called_on_init(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        # At minimum CREATE TABLE should have been executed
        assert conn.execute.called

    def test_link_table_attributes(self):
        engine, _ = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        assert store.memory_link_table == "test_sources_memory_links"
        assert store.skill_link_table == "test_sources_skill_links"

    def test_create_source(self):
        engine, conn = _make_mock_engine()
        result_mock = MagicMock()
        result_mock.lastrowid = 42
        conn.execute.return_value = result_mock

        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        # Reset mock counts after create_table
        conn.execute.reset_mock()

        result = store.create_source(
            source_type="conversation",
            content="hello world",
            metadata={"key": "val"},
            user_id="u1",
        )
        assert result["id"] == 42
        assert result["source_type"] == "conversation"
        assert result["content"] == "hello world"
        assert result["metadata"] == {"key": "val"}

    def test_create_source_scope_propagation(self):
        """All four scope IDs must be bound into the INSERT params."""
        engine, conn = _make_mock_engine()
        result_mock = MagicMock()
        result_mock.lastrowid = 99
        conn.execute.return_value = result_mock

        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        conn.execute.return_value = result_mock

        result = store.create_source(
            source_type="conversation",
            content="c",
            user_id="u1",
            agent_id="a1",
            run_id="r1",
            actor_id="act1",
        )
        assert result["id"] == 99
        assert result["run_id"] == "r1"
        assert result["actor_id"] == "act1"

        # The bound parameter dict is the second positional arg to conn.execute
        params = conn.execute.call_args.args[1]
        assert params["user_id"] == "u1"
        assert params["agent_id"] == "a1"
        assert params["run_id"] == "r1"
        assert params["actor_id"] == "act1"

    def test_get_source_found(self):
        engine, conn = _make_mock_engine()
        fake_row = {
            "id": 1,
            "source_type": "file",
            "content": "data",
            "metadata": '{"a":1}',
            "user_id": "u1",
            "agent_id": None,
            "run_id": "r1",
            "actor_id": None,
            "created_at": "2025-01-01T00:00:00",
        }
        conn.execute.return_value.mappings.return_value.fetchone.return_value = fake_row

        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        result = store.get_source(1)
        assert result is not None
        assert result["id"] == 1
        assert result["metadata"] == {"a": 1}  # parsed from JSON string
        assert result["run_id"] == "r1"

    def test_get_source_not_found(self):
        engine, conn = _make_mock_engine()
        conn.execute.return_value.mappings.return_value.fetchone.return_value = None

        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        result = store.get_source(999)
        assert result is None

    # ---------------------------------------------------------------- #
    # Linking: memory / skill all share _link / _unlink
    # ---------------------------------------------------------------- #

    def test_link_memory(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        rc_mock = MagicMock()
        rc_mock.rowcount = 1
        conn.execute.return_value = rc_mock

        result = store.link_memory(source_id=1, memory_id=10)
        assert result is True
        # The INSERT should target the memory link table
        sql = str(conn.execute.call_args.args[0])
        assert store.memory_link_table in sql
        assert "memory_id" in sql

    def test_link_memory_duplicate_returns_false(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        rc_mock = MagicMock()
        rc_mock.rowcount = 0   # ON DUPLICATE KEY UPDATE = 0 rowcount
        conn.execute.return_value = rc_mock

        assert store.link_memory(source_id=1, memory_id=10) is False

    def test_link_skill(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        rc_mock = MagicMock()
        rc_mock.rowcount = 1
        conn.execute.return_value = rc_mock

        result = store.link_skill(source_id=1, skill_id=20)
        assert result is True
        sql = str(conn.execute.call_args.args[0])
        assert store.skill_link_table in sql
        assert "skill_id" in sql

    def test_unlink_skill_returns_true_when_row_removed(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        rc_mock = MagicMock()
        rc_mock.rowcount = 1
        conn.execute.return_value = rc_mock

        assert store.unlink_skill(source_id=1, skill_id=20) is True
        sql = str(conn.execute.call_args.args[0])
        assert "DELETE" in sql.upper()
        assert store.skill_link_table in sql

    def test_get_sources_for_skill_uses_skill_link_table(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        conn.execute.return_value.mappings.return_value.fetchall.return_value = []

        store.get_sources_for_skill(skill_id=42)
        sql = str(conn.execute.call_args.args[0])
        assert store.skill_link_table in sql
        assert "skill_id" in sql

    def test_get_skills_for_source_returns_id_list(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        # Reverse query returns rows of {target_col: id}
        conn.execute.return_value.mappings.return_value.fetchall.return_value = [
            {"skill_id": 1}, {"skill_id": 2}, {"skill_id": 3},
        ]

        ids = store.get_skills_for_source(source_id=99)
        assert ids == [1, 2, 3]
        sql = str(conn.execute.call_args.args[0])
        assert store.skill_link_table in sql

    # ---------------------------------------------------------------- #
    # Memory-side symmetric tests (mirrors of skill-side above)
    # ---------------------------------------------------------------- #

    def test_unlink_memory_returns_true_when_row_removed(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        rc_mock = MagicMock()
        rc_mock.rowcount = 1
        conn.execute.return_value = rc_mock

        assert store.unlink_memory(source_id=1, memory_id=10) is True
        sql = str(conn.execute.call_args.args[0])
        assert "DELETE" in sql.upper()
        assert store.memory_link_table in sql

    def test_get_sources_for_memory_uses_memory_link_table(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        conn.execute.return_value.mappings.return_value.fetchall.return_value = []

        store.get_sources_for_memory(memory_id=42)
        sql = str(conn.execute.call_args.args[0])
        assert store.memory_link_table in sql
        assert "memory_id" in sql

    def test_get_memories_for_source_returns_id_list(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()
        # Reverse query returns rows of {target_col: id}
        conn.execute.return_value.mappings.return_value.fetchall.return_value = [
            {"memory_id": 1}, {"memory_id": 2}, {"memory_id": 3},
        ]

        ids = store.get_memories_for_source(source_id=99)
        assert ids == [1, 2, 3]
        sql = str(conn.execute.call_args.args[0])
        assert store.memory_link_table in sql

    # ---------------------------------------------------------------- #
    # delete_source: must sweep both link tables
    # ---------------------------------------------------------------- #

    def test_delete_source_sweeps_all_link_tables(self):
        engine, conn = _make_mock_engine()
        store = OceanBaseSourceStore(engine=engine, table_name="test_sources")
        conn.execute.reset_mock()

        delete_result = MagicMock()
        delete_result.rowcount = 1
        conn.execute.return_value = delete_result

        result = store.delete_source(1)
        assert result is True

        # Inspect all DELETE statements -- expect one per link table + one main
        sqls = [str(c.args[0]) for c in conn.execute.call_args_list]
        assert any(store.memory_link_table in s for s in sqls)
        assert any(store.skill_link_table in s for s in sqls)
        assert any(store.table_name in s and "FROM `test_sources`" in s for s in sqls)


# --------------------------------------------------------------------------- #
# Migration: RENAME old `_links` -> `_memory_links`
# --------------------------------------------------------------------------- #


class TestLegacyLinkTableMigration:
    """Verify the one-shot rename when an old single-target link table exists."""

    def _engine_with_table_existence(self, legacy_exists, memory_links_exists):
        """Return a mock engine that responds to information_schema probes.

        ``conn.execute(<information_schema query>, {"name": <name>}).fetchone()``
        returns a row when the corresponding flag is True, else None.
        """
        engine, conn = _make_mock_engine()

        def execute_side_effect(stmt, params=None):
            stmt_text = str(stmt).lower()
            result = MagicMock()
            if "information_schema.tables" in stmt_text and params:
                name = params.get("name")
                if name and name.endswith("_links") and not name.endswith("_memory_links"):
                    # legacy probe
                    result.fetchone.return_value = MagicMock() if legacy_exists else None
                elif name and name.endswith("_memory_links"):
                    result.fetchone.return_value = MagicMock() if memory_links_exists else None
                else:
                    result.fetchone.return_value = None
            else:
                result.fetchone.return_value = None
                result.lastrowid = 1
                result.rowcount = 1
                result.mappings.return_value.fetchone.return_value = None
                result.mappings.return_value.fetchall.return_value = []
            return result

        conn.execute.side_effect = execute_side_effect
        return engine, conn

    def test_rename_when_legacy_exists_and_new_does_not(self):
        engine, conn = self._engine_with_table_existence(
            legacy_exists=True, memory_links_exists=False
        )
        OceanBaseSourceStore(engine=engine, table_name="srcs")

        # Look for the RENAME TABLE statement
        rename_calls = [
            str(c.args[0]) for c in conn.execute.call_args_list
            if "RENAME TABLE" in str(c.args[0])
        ]
        assert len(rename_calls) == 1
        assert "srcs_links" in rename_calls[0]
        assert "srcs_memory_links" in rename_calls[0]

    def test_no_rename_when_no_legacy_table(self):
        engine, conn = self._engine_with_table_existence(
            legacy_exists=False, memory_links_exists=False
        )
        OceanBaseSourceStore(engine=engine, table_name="srcs")

        rename_calls = [
            c for c in conn.execute.call_args_list
            if "RENAME TABLE" in str(c.args[0])
        ]
        assert rename_calls == []

    def test_no_rename_when_both_tables_exist(self):
        """Both legacy and new exist -- skip rename to avoid clobbering data."""
        engine, conn = self._engine_with_table_existence(
            legacy_exists=True, memory_links_exists=True
        )
        OceanBaseSourceStore(engine=engine, table_name="srcs")

        rename_calls = [
            c for c in conn.execute.call_args_list
            if "RENAME TABLE" in str(c.args[0])
        ]
        assert rename_calls == []


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


class TestSourceStoreConfig:
    """Test SourceStoreConfig model."""

    def test_defaults(self):
        from powermem.configs import SourceStoreConfig
        cfg = SourceStoreConfig()
        assert cfg.enabled is False
        assert cfg.collection_name is None

    def test_enabled(self):
        from powermem.configs import SourceStoreConfig
        cfg = SourceStoreConfig(enabled=True, collection_name="my_sources")
        assert cfg.enabled is True
        assert cfg.collection_name == "my_sources"


# --------------------------------------------------------------------------- #
# Regression: _table_exists must not poison outer transaction state
# (Round 3 P0 -- caught by L3 smoke against real OceanBase on SQLAlchemy 2.x)
# --------------------------------------------------------------------------- #


class TestTableExistsTransactionInvariant:
    """Real SQLAlchemy engine check: _table_exists must leave the connection
    in a clean (non-autobegun) state so the caller can immediately enter a
    new explicit ``with conn.begin():`` block.

    Before the Round 3 fix, _table_exists did a bare ``conn.execute()``
    outside any explicit transaction; SQLAlchemy 2.x autobegins on that,
    leaving the connection with a pending transaction. The next
    ``with conn.begin():`` then raised InvalidRequestError.

    SQLite's autobegin semantics differ slightly from MySQL, but the
    structural invariant we assert here -- "after _table_exists returns,
    a new explicit begin() can be entered" -- is the exact bug surface
    that broke real OceanBase, and it reproduces on any 2.x engine that
    autobegins (the SQLite default driver does).
    """

    def test_table_exists_leaves_clean_tx_state_so_outer_begin_can_start(self):
        engine = _real_create_engine("sqlite:///:memory:")
        try:
            with engine.connect() as conn:
                # Probe a table that does not exist; helper must catch the
                # information_schema-absent error and still leave conn clean.
                exists = OceanBaseSourceStore._table_exists(conn, "no_such_table")
                assert exists is False

                # KEY ASSERTION: the connection must be ready for an explicit
                # transaction. Before the fix, this raised:
                #   InvalidRequestError: This connection has already
                #   initialized a SQLAlchemy Transaction() object via begin()
                #   or autobegin; can't call begin() here unless rollback()
                #   or commit() is called first.
                with conn.begin():
                    conn.execute(_real_text("SELECT 1"))
        finally:
            engine.dispose()

    def test_table_exists_then_repeated_outer_begins_succeed(self):
        """Mimic create_table()'s flow: begin -> _table_exists -> begin."""
        engine = _real_create_engine("sqlite:///:memory:")
        try:
            with engine.connect() as conn:
                with conn.begin():
                    conn.execute(_real_text(
                        "CREATE TABLE IF NOT EXISTS t1 (id INTEGER PRIMARY KEY)"
                    ))

                # The bug site: bare-execute probe between two begin() blocks.
                OceanBaseSourceStore._table_exists(conn, "t1")

                # Second outer begin -- this is line 65 of create_table() in
                # the original flow. Must not raise.
                with conn.begin():
                    conn.execute(_real_text(
                        "CREATE TABLE IF NOT EXISTS t2 (id INTEGER PRIMARY KEY)"
                    ))
        finally:
            engine.dispose()
