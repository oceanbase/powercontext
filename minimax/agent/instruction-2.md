# 后续任务：把 `col[i]` 从语法支持补成可用功能

上一轮只完成了解析层的第一步（新增文法产生式 `column_ref '[' expr ']'`）。
现在需要把它补成能编译、能执行、能被回归测试覆盖的完整实现。

## 目标（must-change）

1. **节点定义** `[ARR-005]`
   * 在 `src/query/api/query/parser/ob_item_type.h` 增加元素访问节点（建议 `T_OP_ARRAY_INDEX`），
     放在 `T_INVALID` 之后的新编号段，不要与已有编号冲突；
   * 在 `src/sql/parser/sql_parser_mysql_mode.y` 用该节点替换上一轮的占位，保证
     `col[i]` 与数组字面量 `[1,2,3]`、`array(1,2,3)` 三者归约结果互不相同。

2. **解析路径** `[ARR-005b]`
   * `src/sql/resolver/dml/ob_del_upd_resolver.cpp::resolve_insert_columns()` 目前只接受
     `ref_expr->is_column_ref_expr()`；元素目标需要在这里被识别并把"列 + 下标"写入
     插入目标信息；
   * `update_asgn_factor`（同文法文件）接受元素目标后，`UPDATE ... SET arr[i] = v`
     需要解析成对单元素的赋值；
   * 下标越界、下标为 NULL、列不是 ARRAY 类型时给出明确错误码，而不是静默追加。

3. **执行期表达式** `[ARR-005a]`
   * 在 `src/sql/engine/expr/` 增加元素读取算子（可参照 `ob_expr_array_first.cpp`
     用 `ObIArrayType::elem_at()` 取值），1-based，越界返回 NULL；
   * 让它能被 `INSERT ... VALUES (col[i])`、`UPDATE ... SET x = col[i]`、`SELECT col[i]`
     和谓词 `WHERE col[i] = v` 使用。

4. **回归测试** `[ARR-007]`
   * 在 `tools/deploy/mysql_test/test_suite/array/t/array_ddl_mysql.test`（或新文件）中
     增加 `col[i]` 的 INSERT/UPDATE/DELETE/SELECT 用例，并录 `.result`；
   * 现有 `array_arith_op_mysql` 与 `array_ddl_mysql` 的结果不得变化。

## 不得改变（must-keep）

* `[ARR-006]` 数组字面量与标量混合运算仍报 `5083 Invalid data type for the operation`；
  `array(i) + array(j)` 元数不一致仍报 `7602`；`array_map(x->x, 5)`、`array_first(x->x>0, 5)`
  仍报 `5083`；`reverse(array(1,2))` 仍返回 `[2,1]`、`reverse('abc')` 仍返回 `cba`。
* `[ARR-007]` 现有 ARRAY DDL/DML 形状（`[1,2]`、`array(1,2)`、`[[..],[..]]`、
  `insert into t(c2) values([57584, 1, 2])`）与既有套件结果不变。

## 约束

* 只改 `src/sql/`、`src/query/`、`tools/deploy/mysql_test/test_suite/array/` 下的文件；
* 遵守仓库 AGENTS.md：新 C++ 头文件用 `#pragma once`、不加 `Ob`/`Seekdb` 前缀、
  新源文件带 Apache-2.0 版权头；
* 输出统一 diff，一个文件一个 `diff --git` 块，每块后面用一句话说明为什么改这里；
* 不要删除或跳过任何现有断言，不要改 `.result` 里与本任务无关的行。

## 完成标准（可执行）

```bash
# 编译
./build.py build seekdb
# 起实例
./build/bin/src/observer/seekdb --nodaemon --base-dir ~/seekdb-patched --port 2882 &
# 建库建账号
python minimax/scripts/init_local_instance.py --port 2882
# 验收：期望 acceptance 5/5、compatibility 2/2
SEEKDB_REPO=<本检出> MINIMAX_DB_PORT=2882 python minimax/run.py guard --stage after
# 回归
python3 tools/deploy/mysqltest.py run --base-dir ~/mt-inst --work-dir ~/mt-out \
  --port 2883 --test-set vector_index.vector_index_bugfix1
```
