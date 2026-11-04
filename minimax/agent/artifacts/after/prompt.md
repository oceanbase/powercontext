You are changing the seekdb C++ server (OceanBase SQL engine).
Below is the developer context assembled for this task: the specs that apply,
the exact code locations, the existing tests that must keep passing, and what the
running server does today.

Rules:
1. Only satisfy the 'must-change' specs. Never change behaviour covered by a
   'must-keep' spec.
2. Output unified diffs only, one per file, each starting with 'diff --git'.
3. Keep the change small and explain in one sentence per file why it is needed.

Task instruction: # Task for MiniMax

You are working on **seekdb** (OceanBase's standalone SQL engine, C++), in a git
checkout of `https://github.com/oceanbase/seekdb` at the revision the context was
captured from.

## What to change

Implement element-wise access to ARRAY columns with the 1-based `col[i]` form, in the
places the specs below say it is missing:

- `INSERT`: `insert into t(id, arr[2]) values (1, 42)` must write element 2 of `arr`
  into the new row and use the column defaults for the other elements. `[ARR-001]`
  `[ARR-005]` `[ARR-005b]`
- `UPDATE`: `update t set arr[2] = 20` must rewrite element 2 of `arr` for every
  matched row. `[ARR-002]` `[ARR-005]` `[ARR-005b]`
- `DELETE` and any predicate: `delete from t where arr[4] = 60` must match on the
  element. `[ARR-003]` `[ARR-005]`
- Reads: `select arr[1] from t` and `select [10,20,30][2]` must return that element,
  and must return `NULL` when the index is out of range. `[ARR-004]` `[ARR-005a]`

The parser must build a dedicated node for `col[i]` so it is not confused with the
ARRAY literal `[1,2,3]`; the resolver must accept the node where it already accepts a
column reference for INSERT target lists and UPDATE assignments.

## What must not change

- ARRAY literal arithmetic keeps today's results and error codes. `[ARR-006]`
- `array_map` / `array_first` with a scalar argument keep returning
  `5083 Invalid data type for the operation`, `reverse(array(1,2))` keeps returning
  `[2,1]` and `reverse('abc')` keeps returning `cba`. `[ARR-006]`
- The current ARRAY DDL/DML shapes and the existing `array_arith_op_mysql` /
  `array_ddl_mysql` cases keep passing. `[ARR-007]`

## Environment facts

The server under test is `OceanBase seekdb 1.4.0.0`, build
`100000172026082615-0003f8d4b32a2cfe3b75ae8f4dbe26fcee994541`. Today every
`col[i]` form returns `ERROR 1064 (42000): You have an error in your SQL syntax`.
`SELECT ARRAY_FIRST`-style helpers are the only way to reach a single element.

## Output contract

Answer with unified diffs only: one `diff --git a/... b/...` block per modified file,
plus one sentence per file saying why the change is needed. Do not restate the task,
do not include explanations outside the diffs, and do not touch files outside
`src/sql/`, `src/query/` and `tools/deploy/mysql_test/test_suite/array/`.


== TASK ==
no saved task state (run 'state save' first)

== SPECS THAT APPLY (read from seekdb) ==
[ARR-001] (must-change) Element-wise INSERT target `col[i]` is rejected by the SQL grammar
  INSERT ... (c1, arr[i], c3) VALUES (...) must be accepted by the parser and resolved as a partial write of element i of the ARRAY column arr. Today the statement fails in the grammar before name resolution runs.
  observed: ERROR 1064: ... near '[1], bar[2], bar[3], bar[4], label) VALUES (...)' at line 1
  observed: ERROR 1064: ... near '[2]) VALUES (3, 'row3', 52)' at line 1
  source: src/sql/parser/sql_parser_mysql_mode.y:8252 (column_list) sha=f22a532659799685
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=3aa0dcda0a564752
  source: src/sql/parser/sql_parser_mysql_mode.y:8058 (single_table_insert) sha=3459a6519ac786e8
[ARR-002] (must-change) Element-wise UPDATE target `col[i]` is rejected by the SQL grammar
  UPDATE t SET arr[i] = v must be accepted and must rewrite only element i of arr in every updated row.
  observed: ERROR 1064: ... near '[3] = 7 WHERE id = 1' at line 1
  source: src/sql/parser/sql_parser_mysql_mode.y:3918 (update_asgn_factor) sha=2cf2f5e5dd46ed29
  source: src/sql/parser/sql_parser_mysql_mode.y:3907 (update_asgn_list) sha=c5057280c1eaa446
[ARR-003] (must-change) Element-wise DELETE predicate `col[i] = v` is rejected by the SQL grammar
  The predicate `arr[i] = v` must be usable wherever a boolean expression is allowed (starting with DELETE ... WHERE) and must select the rows whose element i equals v.
  observed: ERROR 1064: ... near '[3] = 60' at line 1
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=3aa0dcda0a564752
  source: src/sql/engine/dml/ob_table_delete_op.cpp:106 (ObTableDeleteOp::inner_open) sha=12340f47be0f7e00
[ARR-004] (must-change) Element reads `col[i]` are unsupported in expressions
  `SELECT arr[i]` and any expression that reads one element must be supported once ARR-005 provides the node.
  observed: ERROR 1064: ... near '[2] FROM dual' at line 1
  source: src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:2116 (set_geo_func_name) sha=a9575c7a4fd88aa9
[ARR-005] (must-change) Parser must expose an ARRAY_INDEX node for `col[i]`
  The grammar must build an element-access node for `<column-ref> '[' expr ']'`, and that node must be distinguishable from the ARRAY literal node `'[' expr_list ']'`.
  observed: No node exists: `SELECT slots[1] ...` fails in the lexer/parser, and the only node the grammar can build for brackets is T_FUN_SYS_ARRAY (1737), which is the ARRAY literal.
  source: src/query/api/query/parser/ob_item_type.h:766 (T_FUN_SYS_ARRAY) sha=ecbbec638e0d1178
  source: src/query/api/query/parser/ob_item_type.h:991 (T_COLUMN_REF) sha=aa387cb206e7bb26
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=3aa0dcda0a564752
[ARR-005a] (must-change) The element-access expression reads `col[i]` in SELECT
  `SELECT arr[i] FROM t` must return the element at 1-based position i, and must return NULL when i is out of range or NULL.
  observed: unsupported today (no node, see ARR-005)
  source: src/sql/engine/expr/ob_expr_operator_factory.cpp:440 (ObExprOperatorFactory) sha=70da91d5cc847951
  source: src/sql/engine/expr/ob_expr_array_first.cpp:0 (ob_expr_*) sha=missing
[ARR-005b] (must-change) Writes targets resolve through the shared `insert_columns` / assignment path
  Element targets must be accepted by the resolver entry points that already validate INSERT target columns and UPDATE assignment columns, and an out-of-range element index must raise a clear error instead of silently appending.
  observed: `resolve_insert_columns()` accepts only `ref_expr->is_column_ref_expr()`; `update_asgn_factor` never reaches it.
  source: src/sql/resolver/dml/ob_del_upd_resolver.cpp:2123 (ObDelUpdResolver::resolve_insert_columns) sha=e5152474b636dced
  source: src/sql/resolver/dml/ob_insert_resolver.cpp:396 (ObInsertResolver::resolve_insert_field) sha=7a503484c8f00cce
[ARR-006] (must-keep) Existing ARRAY literal, helper and vector overload rules keep today's results
  ARRAY literal + ARRAY literal with different cardinality keeps its current error, ARRAY +/- scalar keeps its current error, the lambda helpers must reject a scalar number input and `reverse()` must keep its scalar and array behaviour; none of these may change to support element access, and none may stop working.
  observed: array(1,2)+array(3) -> 7602 'array type mismatch found between definition and data'
  observed: array(1,2)+3 -> 5083 'Invalid data type for the operation'
  source: src/sql/engine/expr/ob_expr_add.cpp:46 (ObExprAdd::calc_result_type2) sha=6b210a78f039b0e1
  source: src/sql/engine/expr/ob_expr_array_map.cpp:36 (ObExprArrayMap) sha=23142f91265330ad
  source: src/sql/engine/expr/ob_expr_reverse.cpp:28 (ObExprReverse) sha=c806d07c468259c1
[ARR-007] (must-keep) Deprecated vector/index paths and current ARRAY DML forms must keep working
  `analyze table`, of an ARRAY column, the array_arith_op / array_ddl suites' current statements, and the vector `l2_distance` ordering must keep their current results; adding element access must not disturb them.
  observed: tests/seatbelt/array_arith_op_mysql.test + array_ddl_mysql.test pass on the baseline build
  source: tools/deploy/mysql_test/test_suite/array/t/array_ddl_mysql.test:0 sha=missing
  source: tools/deploy/mysql_test/test_suite/array/t/array_arith_op_mysql.test:0 sha=missing

== CHECKS ==
  ARR-001 <- live_server
  ARR-001 <- parser_only
  ARR-002 <- live_server
  ARR-002 <- live_server
  ARR-003 <- live_server
  ARR-004 <- live_server
  ARR-006 <- live_server
  ARR-007 <- live_server

== EXISTING TESTS THAT MUST KEEP PASSING ==
  tools/deploy/mysql_test/test_suite/array/t/array_arith_op_mysql.test sha=f7f665ccf7eed66c
  tools/deploy/mysql_test/test_suite/array/t/array_ddl_mysql.test sha=e9dd688e02bda0fb

== ENVIRONMENT: what seekdb does today ==
  TC-COMPAT-01 (pass) matches the unmodified tree
      error 7602  select array(1,2) + array(3)
      error 5083  select array(1,2) + 3
      error 5083  select array_map(x -> x, 5)
      error 5083  select array_first(x -> x > 0, 5)
              ok  select reverse(array(1,2))
              ok  select reverse('abc')
  TC-COMPAT-02 (pass) matches the unmodified tree
              ok  drop table if exists tc_ddl
              ok  create table tc_ddl(c1 int, c2 array(int), c3 array(bigint))
              ok  insert into tc_ddl values (1, [1, 2, 3], [4, 5, 6])
              ok  insert into tc_ddl(c2) values ([57584, 1, 2])
              ok  select c1, reverse(c2), reverse(c3) from tc_ddl order by c1
  TC-DELETE-01 (fail) #3: expected success, got error 1064: You have an error in your SQL syntax; check the manual that corresponds to your OceanBase ; #3: rows expected [['2', '[9,3,2,1]']], got []
              ok  drop table if exists tc_delete
              ok  create table tc_delete(id int not null primary key, slots array(int) not null, note varchar(32))
              ok  insert into tc_delete values (1, [1, 2, 3, 4], 'drop'), (2, [1, 2, 3, 9], 'keep')
      error 1064  delete from tc_delete where slots[4] = 4
              ok  select id, reverse(slots) from tc_delete order by id
  TC-INSERT-01 (fail) #3: expected success, got error 1064: You have an error in your SQL syntax; check the manual that corresponds to your OceanBase ; #4: rows expected [['1', '[0,0,0,0]', 'widen'], ['2', '[0,42,0,0]', 'target']], got [['1', '[0,0,0,0]', 'widen']]; #5: rows expected [['2']], got []
              ok  drop table if exists tc_insert
              ok  create table tc_insert(id int not null primary key, slots array(int) not null, note varchar(32))
              ok  insert into tc_insert values (1, [0, 0, 0, 0], 'widen')
      error 1064  insert into tc_insert(id, slots[2], note) values (2, 42, 'target')
              ok  select id, reverse(slots), note from tc_insert order by id
              ok  select id from tc_insert where slots = [0, 42, 0, 0] order by id
  TC-PARSER-01 (fail) element-access node: none
  TC-READ-01 (fail) #0: expected success, got error 1064: You have an error in your SQL syntax; check the manual that corresponds to your OceanBase ; #1: expected success, got error 1064: You have an error in your SQL syntax; check the manual that corresponds to your OceanBase ; #5: expected success, got error 1064: You have an error in your SQL syntax; check the manual that corresponds to your OceanBase ; #6: expected success, got error 1064: You have an error in your SQL syntax; check the manual that corresponds to your OceanBase ; #0: rows expected [['30']], got []; #1: rows expected [['null']], got []; #5: rows expected [['5']], got []; #6: rows expected [['null']], got []
      error 1064  select [10,20,30][2]
      error 1064  select [10,20,30][5]
              ok  drop table if exists tc_read
              ok  create table tc_read(id int not null primary key, slots array(int) not null, note varchar(32))
              ok  insert into tc_read values (1, [5, 6, 7, 8], 'r')
      error 1064  select slots[1] from tc_read
      error 1064  select slots[6] from tc_read

== RESULTS OF THE LAST STAGES ==
  after/TC-PARSER-01: fail @ 2026-11-04 11:55:36.561815
  after/TC-COMPAT-02: pass @ 2026-11-04 11:55:36.561815
  after/TC-COMPAT-01: pass @ 2026-11-04 11:55:36.561815
  after/TC-READ-01: fail @ 2026-11-04 11:55:36.561815
  after/TC-DELETE-01: fail @ 2026-11-04 11:55:36.551738
  after/TC-UPDATE-01: fail @ 2026-11-04 11:55:36.551738
