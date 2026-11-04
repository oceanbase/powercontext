# Task for MiniMax

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
