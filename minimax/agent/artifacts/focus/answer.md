diff --git a/src/query/api/query/parser/ob_item_type.h b/src/query/api/query/parser/ob_item_type.h
--- a/src/query/api/query/parser/ob_item_type.h
+++ b/src/query/api/query/parser/ob_item_type.h
@@ -766,18 +766,19 @@
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
   T_FUN_SYS_LAMBDA = 1753,
   T_FUN_SYS_ORA_LOGIN_USER = 1754,
+  T_OP_ARRAY_INDEX = 1755,
diff --git a/src/sql/parser/sql_parser_mysql_mode.y b/src/sql/parser/sql_parser_mysql_mode.y
--- a/src/sql/parser/sql_parser_mysql_mode.y
+++ b/src/sql/parser/sql_parser_mysql_mode.y
@@ -3171,8 +3171,13 @@
 | '[' expr_list ']'
 {
   malloc_non_terminal_node($$, result->malloc_pool_, T_FUN_SYS_ARRAY, 1, $2);
 }
+| column_ref '[' expr ']'
+{
+  malloc_non_terminal_node($$, result->malloc_pool_, T_OP_ARRAY_INDEX, 2, $1, $3);
+}
 | vector_distance_expr
 {
   $$ = $1;
 }