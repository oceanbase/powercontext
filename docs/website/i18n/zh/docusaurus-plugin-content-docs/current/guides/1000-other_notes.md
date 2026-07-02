# 其他注意事项 {#other-notes}

本文档包含使用 PowerMem 时的重要注意事项和考虑事项。请仔细阅读这些要点，以避免常见问题并确保正确集成。

---

## 注意事项 1：JavaScript/TypeScript 中的 Memory ID 精度 {#note-1-memory-id-precision-in-javascripttypescript}

### 概述 {#overview}

PowerMem 中的 `memory_id` 字段是一个 64 位的 Snowflake 整数。当 JavaScript/TypeScript 客户端解析包含此 ID 的 JSON 响应时，由于该值可能超过 JavaScript 的 53 位安全整数限制（`Number.MAX_SAFE_INTEGER = 2^53 - 1`），可能会导致精度丢失。

### 问题 {#the-problem}

JavaScript 的 `Number` 类型使用 IEEE 754 双精度浮点格式，该格式只能安全表示最大为 `2^53 - 1`（9,007,199,254,740,991）的整数。任何大于此值的整数在从 JSON 解析时可能会丢失精度。

### 示例 {#example}

当创建并检索一个记忆时：
```python
from powermem import create_memory

memory = create_memory()
res = memory.get_all(user_id="hsy")
print(res)
```
响应中包含一个 `memory_id`，例如 `653247040368672768`。然而，当在 JavaScript/TypeScript 中解析此 JSON 时：
```javascript
const response = JSON.parse('{"id": 653247040368672768, ...}');
console.info(response.id);  // 输出：653247040368672800（精度已丢失！）
```
值 `653247040368672768` 因精度丢失变为 `653247040368672800`。

### 解决方案 {#solution}

在使用 JavaScript/TypeScript 客户端或任何可能解析 JSON 的环境时，**将 `memory_id` 视为字符串**而不是数字，以保留精度。

#### Python：转换为字符串以用于 JSON {#python-converting-to-string-for-json}

在向 JavaScript/TypeScript 客户端发送数据时，将 `memory_id` 转换为字符串：
```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# 获取记忆，memory_id 在 Python 中以 int 返回
result = memory.get_all(user_id="user123")
memory_id = result['results'][0]['id']  # int 类型

# 发送给 JS/TS 客户端时，通过 JSON 传输前转换为字符串
json_response = {
    "id": str(memory_id),  # 转换为字符串以用于 JSON
    "memory": result['results'][0]['memory'],
    "user_id": result['results'][0]['user_id'],
    # ... 其他字段
}

# 使用带自定义序列化器的 dumps 时
import json
from json import dump, dumps

def serialize_memory_id(obj):
    """memory_id 的自定义 JSON 序列化器"""
    if isinstance(obj, dict) and 'id' in obj:
        obj = obj.copy()
        obj['id'] = str(obj['id'])
    return obj

# 应用于所有结果
results = result.get('results', [])
for item in results:
    item['id'] = str(item['id'])

json_string = dumps(results)
```
#### JavaScript/TypeScript: 解析为字符串 {#javascripttypescript-parsing-as-string}

在接收 JSON 响应时，确保将 `memory_id` 解析为字符串：
```typescript
// 选项 1：使用自定义 JSON reviver
const response = JSON.parse(jsonString, (key, value) => {
  if (key === 'id' && typeof value === 'number' && value > Number.MAX_SAFE_INTEGER) {
    return String(value);
  }
  return value;
});

// 选项 2：解析后转换（如果已经丢失精度，此方法无效）
// 更好的做法是确保服务端以字符串发送
const response = JSON.parse(jsonString);
if (typeof response.id === 'number') {
  response.id = String(response.id);
}

// 选项 3：使用保留大整数的库
// 例如使用 json-bigint
import JSONbig from 'json-bigint';
const response = JSONbig.parse(jsonString);
```
#### FastAPI 示例 {#fastapi-example}

如果您正在构建一个返回记忆数据的 FastAPI 端点：
```python
from fastapi import FastAPI
from powermem import Memory, auto_config
import json
from json import dump, dumps

app = FastAPI()
config = auto_config()
memory = Memory(config=config)

@app.get("/memories")
async def get_memories(user_id: str):
    result = memory.get_all(user_id=user_id)

    # 将所有结果中的 memory_id 转换为字符串
    results = result.get('results', [])
    for item in results:
        item['id'] = str(item['id'])

    return {"results": results}
```
### 最佳实践 {#best-practices}

1. **在序列化为 JSON 供 JavaScript/TypeScript 客户端使用时，始终将 `memory_id` 转换为字符串**
2. **在接收 JSON 响应时，在 JavaScript/TypeScript 中将 `memory_id` 解析为字符串**
3. **在 API 请求/响应模式中为 `memory_id` 使用字符串类型**，以适配 JS/TS 客户端
4. **在 API 文档中记录此行为**，如果您正在构建集成

### 内部存储 {#internal-storage}

请注意，PowerMem 在内部仍然使用 64 位整数来存储和操作 `memory_id`。仅在以下情况下需要转换为字符串：
- 序列化为 JSON 供外部客户端使用时
- 通过 API 端点传递 `memory_id` 时
- 在需要精度的上下文中记录或显示 `memory_id` 值时

### 相关问题 {#related-issues}

有关更多详细信息和讨论，请参阅 [GitHub Issue #91](https://github.com/oceanbase/powermem/issues/91)。

---
