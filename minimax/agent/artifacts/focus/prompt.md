OUTPUT CONTRACT (mandatory)
- Reply with one or more unified diffs, each starting with 'diff --git a/<path> b/<path>'.
- Use only the file paths listed in the context; the 'source:' lines carry current code.
- Include correct '@@ -l,s +l,s @@' hunk headers; context lines start with one space.
- You may add ONE sentence per file after its diff; no other prose.
- Do not output shell commands, XML/HTML tags, or questions. If a file must be created,
  say so in that one sentence.

Task instruction:
# 聚焦任务：只做解析层（ARR-005）

这是一次范围很小的修改，只改两个文件，不要动其他文件。

## 目标

让文法能为 `col[i]` 建立**独立的元素访问节点**，而不是把它当成数组字面量。

1. `src/query/api/query/parser/ob_item_type.h`
   新增枚举 `T_OP_ARRAY_INDEX`（放在现有 ARRAY 相关号段之后，取一个未被占用的号）。

2. `src/sql/parser/sql_parser_mysql_mode.y`
   在表达式产生式里新增一条：`column_ref '[' expr ']'`，用 `T_OP_ARRAY_INDEX`
   建两子节点的非终结节点（列引用 + 下标表达式）。

## 约束

- 不要修改 `'[' expr_list ']'`（数组字面量）与 `ARRAY '(' expr_list ')'` 这两条产生式；
- 不要动 `array_ddl_mysql.test` / `array_arith_op_mysql.test`；
- 只输出这两个文件的 unified diff，hunk 头必须正确；
- **上下文行必须逐字复制**：标识符、下划线、缩进、逗号一个都不能改（例如
  `T_FUNC_SYS_LAMBDA` 不能写成 `T_FUN_SYS_LAMBDA`）。补丁会被 `git apply` 严格校验。

## 不要求

本任务**不要求**实现解析、执行与回归（那些属于 ARR-001/002/003/004/005a/005b）。


Rules of the change:
1. Only satisfy the 'must-change' specs. Never change behaviour covered by a
   'must-keep' spec.
2. Keep the change small; do not restate the task.

Developer context assembled for this task (specs, code locations with current
source text, existing tests, and what the running server does today):

== TASK ==
seekdb: element-wise ARRAY access (col[i]) in INSERT/UPDATE/DELETE/SELECT  [state revision 10]

== SPECS THAT APPLY (read from seekdb) ==
[ARR-005] (must-change) Parser must expose an ARRAY_INDEX node for `col[i]`
  The grammar must build an element-access node for `<column-ref> '[' expr ']'`, and that node must be distinguishable from the ARRAY literal node `'[' expr_list ']'`.
  observed: No node exists: `SELECT slots[1] ...` fails in the lexer/parser, and the only node the grammar can build for brackets is T_FUN_SYS_ARRAY (1737), which is the ARRAY literal.
  source: src/query/api/query/parser/ob_item_type.h:766 (T_FUN_SYS_ARRAY) sha=5a11683ca9933d03
  ```
  T_FUN_SYS_ARRAY = 1737,
  T_FUN_SYS_L1_DISTANCE = 1738,
  T_FUN_SYS_L2_DISTANCE = 1739,
  T_FUN_SYS_INNER_PRODUCT = 1740,
  T_FUN_SYS_COSINE_DISTANCE = 1741,
  T_FUN_SYS_VECTOR_DIMS = 1742,
  T_FUN_SYS_VECTOR_NORM = 1743,
  T_FUN_SYS_VECTOR_DISTANCE = 1744,
  T_FUNC_SYS_ARRAY_CONTAINS = 1745,
  T_FUN_SYS_NEGATIVE_INNER_PRODUCT = 1746,
  T_FUNC_SYS_ARRAY_OVERLAPS = 1747,
  T_FUNC_SYS_ARRAY_CONTAINS_ALL = 1748,
  T_FUNC_SYS_ARRAY_AGG = 1749,
  T_FUNC_SYS_ARRAY_DISTINCT = 1750,
  T_FUNC_SYS_ARRAY_REMOVE = 1751,
  T_FUNC_SYS_ARRAY_MAP = 1752,
  T_FUNC_SYS_LAMBDA = 1753,
  T_FUN_SYS_ORA_LOGIN_USER = 1754,
  ```
  source: src/query/api/query/parser/ob_item_type.h:991 (T_COLUMN_REF) sha=bbb557b8c4a928c4
  ```
  T_COLUMN_REF = 3290,
  T_RELATION_FACTOR = 3291,
  T_RELATION_FACTOR_IN_HINT = 3292,
  T_RELATION_FACTOR_IN_HINT_LIST = 3293,

  /* parse tree node tags */
  /* insert parse tree node type*/
  T_INSERT = 3294,
  T_INSERT_INTO_CLAUSE = 3295,
  T_VALUE_LIST = 3296,
  T_SINGLE_TABLE_INSERT = 3297,

  T_DELETE = 3298,
  T_SELECT = 3299,
  T_UPDATE = 3300,
  T_MERGE = 3301,
  T_REPLACE = 3302,
  T_EXPLAIN = 3303,
  ```
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=d2c8891d2b76e28c
  ```
| '[' expr_list ']'
{
  malloc_non_terminal_node($$, result->malloc_pool_, T_FUN_SYS_ARRAY, 1, $2);
}
| vector_distance_expr
{
  $$ = $1;
}
| vector_similarity_expr
{
  $$ = $1;
}
| MAP '(' expr_list ')'
{
  malloc_non_terminal_node($$, result->malloc_pool_, T_FUN_SYS_MAP, 1, $3);
}
| ARRAY_AGG '(' opt_distinct expr ')'
{
  ```

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
  TC-DELETE-01 (pass) matches the unmodified tree
              ok  drop table if exists tc_delete
              ok  create table tc_delete(id int not null primary key, slots array(int) not null, note varchar(32))
              ok  insert into tc_delete values (1, [1, 2, 3, 4], 'drop'), (2, [1, 2, 3, 9], 'keep')
      error 1064  delete from tc_delete where slots[4] = 4
              ok  select id, reverse(slots) from tc_delete order by id
  TC-INSERT-01 (pass) matches the unmodified tree
              ok  drop table if exists tc_insert
              ok  create table tc_insert(id int not null primary key, slots array(int) not null, note varchar(32))
              ok  insert into tc_insert values (1, [0, 0, 0, 0], 'widen')
      error 1064  insert into tc_insert(id, slots[2], note) values (2, 42, 'target')
              ok  select id, reverse(slots), note from tc_insert order by id
              ok  select id from tc_insert where slots = [0, 42, 0, 0] order by id
  TC-PARSER-01 (pass) no element-access node in the grammar (the defect reproduces)
  TC-READ-01 (pass) matches the unmodified tree
      error 1064  select [10,20,30][2]
      error 1064  select [10,20,30][5]
              ok  drop table if exists tc_read
              ok  create table tc_read(id int not null primary key, slots array(int) not null, note varchar(32))
              ok  insert into tc_read values (1, [5, 6, 7, 8], 'r')
      error 1064  select slots[1] from tc_read
      error 1064  select slots[6] from tc_read

== RESULTS OF THE LAST STAGES ==
  baseline/TC-PARSER-01: pass @ 2026-11-04 12:02:50.059138
  baseline/TC-COMPAT-02: pass @ 2026-11-04 12:02:50.059138
  baseline/TC-COMPAT-01: pass @ 2026-11-04 12:02:50.059138
  baseline/TC-READ-01: pass @ 2026-11-04 12:02:50.059138
  baseline/TC-DELETE-01: pass @ 2026-11-04 12:02:50.049064
  baseline/TC-UPDATE-01: pass @ 2026-11-04 12:02:50.049064

OUTPUT CONTRACT (mandatory)
- Reply with one or more unified diffs, each starting with 'diff --git a/<path> b/<path>'.
- Use only the file paths listed in the context; the 'source:' lines carry current code.
- Include correct '@@ -l,s +l,s @@' hunk headers; context lines start with one space.
- You may add ONE sentence per file after its diff; no other prose.
- Do not output shell commands, XML/HTML tags, or questions. If a file must be created,
  say so in that one sentence.

Now output the diffs only.
