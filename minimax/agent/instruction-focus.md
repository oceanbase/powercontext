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
