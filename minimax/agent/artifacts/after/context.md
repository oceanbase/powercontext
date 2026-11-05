== TASK ==
seekdb: element-wise ARRAY access (col[i]) in INSERT/UPDATE/DELETE/SELECT  [state revision 6]

== SPECS THAT APPLY (read from seekdb) ==
[ARR-001] (must-change) Element-wise INSERT target `col[i]` is rejected by the SQL grammar
  INSERT ... (c1, arr[i], c3) VALUES (...) must be accepted by the parser and resolved as a partial write of element i of the ARRAY column arr. Today the statement fails in the grammar before name resolution runs.
  observed: ERROR 1064: ... near '[1], bar[2], bar[3], bar[4], label) VALUES (...)' at line 1
  observed: ERROR 1064: ... near '[2]) VALUES (3, 'row3', 52)' at line 1
  source: src/sql/parser/sql_parser_mysql_mode.y:8252 (column_list) sha=cb5ed0971c9e0dbd
  ```
  column_list:
  no_param_column_ref { $$ = $1; }
  | column_list ',' no_param_column_ref
  {
    malloc_non_terminal_node($$, result->malloc_pool_, T_LINK_NODE, 2, $1, $3);
  }
  ;
  
  /*
    there are three type of column_ref:
  ```
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=4618a760f0171faa
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
  ```
  source: src/sql/parser/sql_parser_mysql_mode.y:8058 (single_table_insert) sha=0d5f9547dddc20b6
  ```
  | dml_table_name '(' column_list ')' values_clause
  {
    ParseNode *into_node = NULL;
    ParseNode *column_list = NULL;
    merge_nodes(column_list, result, T_COLUMN_LIST, $3);
    malloc_non_terminal_node(into_node, result->malloc_pool_, T_INSERT_INTO_CLAUSE, 2, $1, column_list);
    refine_insert_values_table($5);
    malloc_non_terminal_node($$, result->malloc_pool_, T_SINGLE_TABLE_INSERT, 4,
                             into_node, /*insert_into_clause*/
                             $5, /*values_clause*/
  ```
[ARR-002] (must-change) Element-wise UPDATE target `col[i]` is rejected by the SQL grammar
  UPDATE t SET arr[i] = v must be accepted and must rewrite only element i of arr in every updated row.
  observed: ERROR 1064: ... near '[3] = 7 WHERE id = 1' at line 1
  source: src/sql/parser/sql_parser_mysql_mode.y:3918 (update_asgn_factor) sha=01b5d6b441c17aed
  ```
  update_asgn_factor:
  no_param_column_ref COMP_EQ expr_or_default
  {
    malloc_non_terminal_node($$, result->malloc_pool_, T_ASSIGN_ITEM, 2, $1, $3);
  }
  ;
  
  /*****************************************************************************
   *
   *	create database grammar
  ```
  source: src/sql/parser/sql_parser_mysql_mode.y:3907 (update_asgn_list) sha=0fa366d7a3c7d7df
  ```
  update_asgn_list:
  update_asgn_factor
  {
    $$ = $1;
  }
  | update_asgn_list ',' update_asgn_factor
  {
    malloc_non_terminal_node($$, result->malloc_pool_, T_LINK_NODE, 2, $1, $3);
  }
  ;
  ```
[ARR-003] (must-change) Element-wise DELETE predicate `col[i] = v` is rejected by the SQL grammar
  The predicate `arr[i] = v` must be usable wherever a boolean expression is allowed (starting with DELETE ... WHERE) and must select the rows whose element i equals v.
  observed: ERROR 1064: ... near '[3] = 60' at line 1
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=4618a760f0171faa
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
  ```
  source: src/sql/engine/dml/ob_table_delete_op.cpp:106 (ObTableDeleteOp::inner_open) sha=139d47757cfb5cd8
  ```
  int ObTableDeleteOp::inner_open()
  {
    int ret = OB_SUCCESS;
    //execute delete with das
    if (OB_FAIL(ObTableModifyOp::inner_open())) {
    } else if (OB_UNLIKELY(MY_SPEC.del_ctdefs_.empty())) {
      ret = OB_ERR_UNEXPECTED;
    } else if (OB_UNLIKELY(iter_end_)) {
      //do nothing
    } else if (OB_FAIL(inner_open_with_das())) {
  ```
[ARR-004] (must-change) Element reads `col[i]` are unsupported in expressions
  `SELECT arr[i]` and any expression that reads one element must be supported once ARR-005 provides the node.
  observed: ERROR 1064: ... near '[2] FROM dual' at line 1
  source: src/sql/resolver/expr/ob_raw_expr_resolver_impl.cpp:2116 (set_geo_func_name) sha=bcbc53cfac66a7ce
  ```
        case T_FUN_SYS_ARRAY: {
          OX(func_expr->set_func_name(N_ARRAY));
          break;
        }
        case T_FUN_SYS_MAP: {
          OX(func_expr->set_func_name(N_MAP));
          break;
        }
        default: {
          ret = OB_INVALID_ARGUMENT;
  ```
[ARR-005] (must-change) Parser must expose an ARRAY_INDEX node for `col[i]`
  The grammar must build an element-access node for `<column-ref> '[' expr ']'`, and that node must be distinguishable from the ARRAY literal node `'[' expr_list ']'`.
  observed: No node exists: `SELECT slots[1] ...` fails in the lexer/parser, and the only node the grammar can build for brackets is T_FUN_SYS_ARRAY (1737), which is the ARRAY literal.
  source: src/query/api/query/parser/ob_item_type.h:766 (T_FUN_SYS_ARRAY) sha=08cacffb92b327e2
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
  ```
  source: src/query/api/query/parser/ob_item_type.h:991 (T_COLUMN_REF) sha=fb2ff7d4b17decd3
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
  ```
  source: src/sql/parser/sql_parser_mysql_mode.y:3171 ('[' expr_list ']') sha=4618a760f0171faa
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
  ```
[ARR-005a] (must-change) The element-access expression reads `col[i]` in SELECT
  `SELECT arr[i] FROM t` must return the element at 1-based position i, and must return NULL when i is out of range or NULL.
  observed: unsupported today (no node, see ARR-005)
  source: src/sql/engine/expr/ob_expr_operator_factory.cpp:440 (ObExprOperatorFactory) sha=346b2b10afcce60d
  ```
          OP_ALLOC[op.get_type()] = ObExprOperatorFactory::alloc<OpClass>; \
          i++;                                        \
        }                                             \
      }();                                            \
    } while(0)
  // When developing two functionally identical expressions (e.g., mid and substr expressions) you can use this macro
  // OriOp is the existing expression, now we want to develop NewOp with the same functionality, using this macro can avoid duplicate code
  // But require OriOp has already registered
  #define REG_SAME_OP(OriOpType, NewOpType, NewOpName, idx_mysql)        \
    do {                                                                 \
  ```
  source: src/sql/engine/expr/ob_expr_array_first.cpp:0 (ob_expr_*) sha=missing
[ARR-005b] (must-change) Writes targets resolve through the shared `insert_columns` / assignment path
  Element targets must be accepted by the resolver entry points that already validate INSERT target columns and UPDATE assignment columns, and an out-of-range element index must raise a clear error instead of silently appending.
  observed: `resolve_insert_columns()` accepts only `ref_expr->is_column_ref_expr()`; `update_asgn_factor` never reaches it.
  source: src/sql/resolver/dml/ob_del_upd_resolver.cpp:2123 (ObDelUpdResolver::resolve_insert_columns) sha=27a0049ab048fe30
  ```
  int ObDelUpdResolver::resolve_insert_columns(const ParseNode *node,
                                               ObInsertTableInfo& table_info)
  {
    int ret = OB_SUCCESS;
    TableItem *table_item = NULL;
    ObDelUpdStmt *del_upd_stmt = get_del_upd_stmt();
    if (OB_ISNULL(del_upd_stmt) || OB_ISNULL(session_info_) || OB_ISNULL(schema_checker_)) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("invalid insert stmt", K(del_upd_stmt), K_(session_info), K_(schema_checker));
    } else if (OB_ISNULL(table_item = del_upd_stmt->get_table_item(0))) {
  ```
  source: src/sql/resolver/dml/ob_insert_resolver.cpp:396 (ObInsertResolver::resolve_insert_field) sha=a78007e05bf3a6d3
  ```
        OB_FAIL(resolve_insert_columns(insert_into.children_[1], insert_stmt->get_insert_table_info()))) {
    }
  
    if (OB_SUCC(ret)) {
      const ObTableSchema *table_schema = NULL;
      OZ(schema_checker_->get_table_schema(
                                           table_item->get_base_table_item().ref_id_,
                                           table_schema));
    }
  ```
[ARR-006] (must-keep) Existing ARRAY literal, helper and vector overload rules keep today's results
  ARRAY literal + ARRAY literal with different cardinality keeps its current error, ARRAY +/- scalar keeps its current error, the lambda helpers must reject a scalar number input and `reverse()` must keep its scalar and array behaviour; none of these may change to support element access, and none may stop working.
  observed: array(1,2)+array(3) -> 7602 'array type mismatch found between definition and data'
  observed: array(1,2)+3 -> 5083 'Invalid data type for the operation'
  source: src/sql/engine/expr/ob_expr_add.cpp:46 (ObExprAdd::calc_result_type2) sha=8879c0008e5ea7f7
  ```
  int ObExprAdd::calc_result_type2(ObExprResType &type,
                                   ObExprResType &type1,
                                   ObExprResType &type2,
                                   ObExprTypeCtx &type_ctx) const
  {
    int ret = OB_SUCCESS;
    static const int64_t CARRY_OFFSET = 1;
    ObScale scale = SCALE_UNKNOWN_YET;
    ObPrecision precision = PRECISION_UNKNOWN_YET;
    const bool is_all_decint_args =
  ```
  source: src/sql/engine/expr/ob_expr_array_map.cpp:36 (ObExprArrayMap) sha=01c7df3b041c2b4a
  ```
  OB_DEF_SERIALIZE(ObExprArrayMapInfo)
  {
    int ret = OB_SUCCESS;
    LST_DO_CODE(OB_UNIS_ENCODE, serialization::make_ser_carray(param_exprs_, param_num_));
    if (OB_SUCC(ret)) {
      if (param_num_ > 0) {
        uint32_t len = sizeof(uint32_t) * param_num_;
        MEMCPY(buf + pos, param_idx_, len);
        pos += len;
      }
  ```
  source: src/sql/engine/expr/ob_expr_reverse.cpp:28 (ObExprReverse) sha=fc123697078ab725
  ```
  ObExprReverse::ObExprReverse(ObIAllocator &alloc) :
      ObStringExprOperator(alloc, T_FUN_SYS_REVERSE, "reverse", 1, VALID_FOR_GENERATED_COL)
  {
  }
  
  ObExprReverse::~ObExprReverse()
  {
  }
  
  int ObExprReverse::do_reverse(const ObString &input_str,
  ```
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