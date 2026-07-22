from __future__ import annotations

import pytest

from powercontext.errors import MemoryBackendConfigurationError
from powercontext.memory import EmbeddingProfile
from powercontext.memory.backends.oceanbase import (
    oceanbase_schema_statements,
    parse_oceanbase_version,
    validate_oceanbase_server,
)

TEST_PROFILE = EmbeddingProfile(
    profile_id="keyword-v1",
    model="keyword",
    dimension=3,
    distance="l2",
    normalization="none",
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("5.7.25-OceanBase_CE-v4.3.5.4", (4, 3, 5, 4)),
        ("OceanBase_CE 4.3.5.4", (4, 3, 5, 4)),
    ],
)
def test_parse_oceanbase_version(value: str, expected: tuple[int, int, int, int]) -> None:
    assert parse_oceanbase_version(value) == expected


def test_oceanbase_server_requires_435_bp3_mysql_mode() -> None:
    with pytest.raises(MemoryBackendConfigurationError, match=r"4\.3\.5\.3"):
        validate_oceanbase_server("5.7.25-OceanBase_CE-v4.3.5.2", "MYSQL")
    with pytest.raises(MemoryBackendConfigurationError, match="MySQL"):
        validate_oceanbase_server("5.7.25-OceanBase_CE-v4.3.5.4", "ORACLE")
    validate_oceanbase_server("5.7.25-OceanBase_CE-v4.3.5.3", "MYSQL")
    validate_oceanbase_server("5.7.25-OceanBase_CE-v4.3.5.4", "MYSQL")


def test_oceanbase_schema_has_one_active_head_table_with_fulltext_and_hnsw() -> None:
    statements = oceanbase_schema_statements("pc_test_", TEST_PROFILE)
    schema = "\n".join(statements)

    assert schema.count("CREATE TABLE IF NOT EXISTS pc_test_memory_entry_heads") == 1
    assert "embedding VECTOR(3)" in schema
    assert "FULLTEXT INDEX pc_test_ftx_memory_entry_heads_text" in schema
    assert "WITH PARSER SPACE" in schema
    assert "VECTOR INDEX pc_test_vidx_memory_entry_heads_embedding" in schema
    assert "WITH (distance=L2, type=hnsw)" in schema
    assert "memory_entry_search_vector" not in schema


def test_oceanbase_schema_rejects_unsafe_prefix_and_invalid_profile() -> None:
    with pytest.raises(MemoryBackendConfigurationError, match="prefix"):
        oceanbase_schema_statements("bad-prefix;drop ", TEST_PROFILE)
    invalid = EmbeddingProfile(
        profile_id="invalid",
        model="invalid",
        dimension=0,
        distance="l2",
        normalization="none",
    )
    with pytest.raises(MemoryBackendConfigurationError, match="dimension"):
        oceanbase_schema_statements("pc_test_", invalid)
