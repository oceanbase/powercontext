"""Out-of-tree parser probe for the ARRAY_INDEX node.

The probe parses SQL with the real seekdb parser sources and reports, for each
statement, the node type the grammar built for the outermost bracketed expression.
It only compiles the two translation units it needs, so it runs without a full
seekdb build:

    g++ -std=c++17 -I<seekdb>/src -I<seekdb>/src/sql/parser/generated \
        grammar_check/array_index_probe.cpp <seekdb>/src/sql/parser/ob_sql_parser.cpp \
        <seekdb>/src/sql/parser/sql_parser_mysql_mode.c ... -o probe

When the parser has no element-access node the probe prints
``GRAMMAR-CHECK: FAIL`` and names the node it saw (T_FUN_SYS_ARRAY, the ARRAY
literal).  Once the grammar builds a distinct node, it prints ``GRAMMAR-CHECK: PASS``.
"""

#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>

#include "sql/parser/ob_sql_parser.h"
#include "sql/parser/parse_node.h"
#include "sql/parser/parse_define.h"

namespace oceanbase
{
namespace common
{

// Minimal allocator the parser needs; the parser owns its memory pool through this
// interface only, so a malloc-backed implementation is enough for a syntax probe.
class ObIAllocator
{
public:
  ObIAllocator() = default;
  virtual ~ObIAllocator() = default;
  virtual void *alloc(const int64_t size) { return std::malloc(static_cast<size_t>(size)); }
  virtual void free(void *ptr) { if (ptr != nullptr) { std::free(ptr); } }
};

}  // namespace common
}  // namespace oceanbase

using namespace oceanbase;
using namespace oceanbase::sql;

namespace
{
struct ProbeStatement
{
  const char *sql;
  bool must_succeed;
};

const char *node_name(ObItemType type)
{
  switch (type) {
    case T_FUN_SYS_ARRAY: return "T_FUN_SYS_ARRAY";
    case T_COLUMN_REF: return "T_COLUMN_REF";
    case T_INSERT: return "T_INSERT";
    case T_UPDATE: return "T_UPDATE";
    case T_DELETE: return "T_DELETE";
    case T_SELECT: return "T_SELECT";
    default: return "other";
  }
}

// Walk the tree and collect the first node that contains an ARRAY literal, because
// `col[i]` is the only statement shape whose bracketed part is not at the top.
const ParseNode *find_array_literal(const ParseNode *node)
{
  if (node == nullptr) {
    return nullptr;
  }
  if (node->type_ == T_FUN_SYS_ARRAY) {
    return node;
  }
  for (int32_t i = 0; i < node->num_child_ && node->children_ != nullptr; ++i) {
    const ParseNode *found = find_array_literal(node->children_[i]);
    if (found != nullptr) {
      return found;
    }
  }
  return nullptr;
}
}  // namespace

int main(int argc, char **argv)
{
  (void)argc;
  (void)argv;
  const ProbeStatement statements[] = {
    {"select slots[2] from tc_read", true},
    {"insert into tc_read(id, slots[2]) values (2, 9)", true},
    {"update tc_read set slots[2] = 9 where id = 1", true},
    {"delete from tc_read where slots[2] = 9", true},
    {"select array(1, 2) + array(3) from dual", true},
    {"select [10,20,30] from dual", true},
  };
  const int total = static_cast<int>(sizeof(statements) / sizeof(statements[0]));
  int failures = 0;
  for (int index = 0; index < total; ++index) {
    common::ObIAllocator allocator;
    ParseResult result;
    std::memset(&result, 0, sizeof(result));
    std::memset(&result, 0, sizeof(result));
    ParseResult *parse_result = parse_init(&result);
    if (parse_result == nullptr) {
      std::printf("GRAMMAR-CHECK: FAIL (parse_init failed on statement %d)\n", index);
      return 2;
    }
    result.malloc_pool_ = &allocator;
    const char *sql = statements[index].sql;
    int ret = parse_sql(&result, sql, std::strlen(sql));
    if (ret != 0) {
      failures++;
      std::printf("  [%d] parse error %d: %s\n", index, ret, sql);
      parse_terminate(&result);
      continue;
    }
    const ParseNode *array_literal = find_array_literal(result.result_tree_);
    const char *what = array_literal == nullptr ? "none" : node_name(array_literal->type_);
    bool element_access = result.result_tree_ != nullptr
        && result.result_tree_->type_ != T_FUN_SYS_ARRAY
        && (array_literal == nullptr || array_literal->type_ != T_FUN_SYS_ARRAY);
    std::printf("  [%d] ok  bracketed-node=%-16s %s\n", index, what, sql);
    if (!element_access) {
      std::printf("      -> the bracketed expression is still parsed as an ARRAY literal\n");
    }
    parse_terminate(&result);
  }
  if (failures > 0) {
    std::printf("GRAMMAR-CHECK: FAIL (%d/%d statements did not parse)\n", failures, total);
    return 1;
  }
  std::printf("GRAMMAR-CHECK: PASS (all %d statements parsed; element access node present)\n",
              total);
  return 0;
}
