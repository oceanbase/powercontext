from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE,
    SHARED_METADATA,
    SOURCE_CURSORS_TABLE,
    SOURCES_TABLE,
)

INNODB_MAX_INDEX_BYTES = 3072
UTF8MB4_MAX_BYTES_PER_CHARACTER = 4


class _UnbudgetedColumnTypeError(AssertionError):
    def __init__(self, column_type: object) -> None:
        super().__init__(f"unbudgeted indexed column type: {column_type!r}")


def _column_budget(column) -> int:
    if isinstance(column.type, String):
        assert column.type.length is not None
        return column.type.length * UTF8MB4_MAX_BYTES_PER_CHARACTER
    if isinstance(column.type, BigInteger):
        return 8
    if isinstance(column.type, Integer):
        return 4
    raise _UnbudgetedColumnTypeError(column.type)


def test_mysql_ddl_uses_mediumblob_for_every_canonical_payload() -> None:
    dialect = mysql.dialect()
    expected = {
        SOURCES_TABLE: "payload",
        ARTIFACTS_TABLE: "content",
        SOURCE_CURSORS_TABLE: "`cursor`",
    }

    for table, column_name in expected.items():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"{column_name} MEDIUMBLOB NOT NULL" in ddl


def test_every_mysql_utf8mb4_key_stays_below_the_innodb_limit() -> None:
    budgets: dict[str, int] = {}
    for table in SHARED_METADATA.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint | ForeignKeyConstraint):
                columns = tuple(column.name for column in constraint.columns)
                name = (
                    str(constraint.name)
                    if constraint.name is not None
                    else f"{table.name}:{type(constraint).__name__}:{','.join(columns)}"
                )
                budgets[name] = sum(_column_budget(column) for column in constraint.columns)
        for index in table.indexes:
            name = str(index.name) if index.name is not None else f"{table.name}:index"
            budgets[name] = sum(_column_budget(column) for column in index.columns)

    assert budgets
    assert max(budgets.values()) == 2560
    assert all(budget < INNODB_MAX_INDEX_BYTES for budget in budgets.values())
