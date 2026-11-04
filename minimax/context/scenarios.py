"""Scenario definitions: what "change this" and "keep that" mean, executably.

Each case lists the SQL a reviewer would type.  The oracle is captured from a
reference run (``scripts/record_baseline.py``) and compared statement by statement,
so a missing feature fails loudly and an unrelated behaviour change fails too.
"""

from __future__ import annotations

import json
from pathlib import Path

CASES = [
    {
        "id": "TC-INSERT-01",
        "spec_id": "ARR-001",
        "check": "live_server",
        "title": "INSERT with an element target writes only that element",
        "kind": "acceptance",
        "sql": [
            "drop table if exists tc_insert",
            "create table tc_insert(id int not null primary key, slots array(int) not null, "
            "note varchar(32))",
            "insert into tc_insert values (1, [0, 0, 0, 0], 'widen')",
            "insert into tc_insert(id, slots[2], note) values (2, 42, 'target')",
            "select id, reverse(slots), note from tc_insert order by id",
            "select id from tc_insert where slots = [0, 42, 0, 0] order by id",
        ],
    },
    {
        "id": "TC-UPDATE-01",
        "spec_id": "ARR-002",
        "check": "live_server",
        "title": "UPDATE with an element target rewrites one element in place",
        "kind": "acceptance",
        "sql": [
            "drop table if exists tc_update",
            "create table tc_update(id int not null primary key, slots array(int) not null, "
            "note varchar(32))",
            "insert into tc_update values (1, [1, 2, 3, 4], 'before')",
            "update tc_update set slots[2] = 20, note = 'after' where id = 1",
            "select id, reverse(slots), note from tc_update",
            "select slots[2] from tc_update",
        ],
    },
    {
        "id": "TC-DELETE-01",
        "spec_id": "ARR-003",
        "check": "live_server",
        "title": "DELETE uses an element predicate",
        "kind": "acceptance",
        "sql": [
            "drop table if exists tc_delete",
            "create table tc_delete(id int not null primary key, slots array(int) not null, "
            "note varchar(32))",
            "insert into tc_delete values (1, [1, 2, 3, 4], 'drop'), (2, [1, 2, 3, 9], 'keep')",
            "delete from tc_delete where slots[4] = 4",
            "select id, reverse(slots) from tc_delete order by id",
        ],
    },
    {
        "id": "TC-READ-01",
        "spec_id": "ARR-004",
        "check": "live_server",
        "title": "Element read returns the element at a 1-based index",
        "kind": "acceptance",
        "sql": [
            "select [10,20,30][2]",
            "select [10,20,30][5]",
            "drop table if exists tc_read",
            "create table tc_read(id int not null primary key, slots array(int) not null, "
            "note varchar(32))",
            "insert into tc_read values (1, [5, 6, 7, 8], 'r')",
            "select slots[1] from tc_read",
            "select slots[6] from tc_read",
        ],
    },
    {
        "id": "TC-COMPAT-01",
        "spec_id": "ARR-006",
        "check": "live_server",
        "title": "Existing array arithmetic, helper and reverse behaviour is unchanged",
        "kind": "compat",
        "sql": [
            "select array(1,2) + array(3)",
            "select array(1,2) + 3",
            "select array_map(x -> x, 5)",
            "select array_first(x -> x > 0, 5)",
            "select reverse(array(1,2))",
            "select reverse('abc')",
        ],
    },
    {
        "id": "TC-COMPAT-02",
        "spec_id": "ARR-007",
        "check": "live_server",
        "title": "Current ARRAY DDL/DML shapes keep working",
        "kind": "compat",
        "sql": [
            "drop table if exists tc_ddl",
            "create table tc_ddl(c1 int, c2 array(int), c3 array(bigint))",
            "insert into tc_ddl values (1, [1, 2, 3], [4, 5, 6])",
            "insert into tc_ddl(c2) values ([57584, 1, 2])",
            "select c1, reverse(c2), reverse(c3) from tc_ddl order by c1",
        ],
    },
    {
        "id": "TC-PARSER-01",
        "spec_id": "ARR-005",
        "check": "parser_only",
        "title": "The grammar builds an element-access node that is not an ARRAY literal",
        "kind": "acceptance",
        "sql": [],
        "grammar": [
            "select slots[2] from tc_read",
            "insert into tc_read(id, slots[2]) values (2, 9)",
            "update tc_read set slots[2] = 9 where id = 1",
            "delete from tc_read where slots[2] = 9",
        ],
        "expected_node": "ARRAY_INDEX",
    },
]


def write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cases": CASES}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path
