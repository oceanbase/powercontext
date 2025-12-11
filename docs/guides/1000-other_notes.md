# Other Notes

This document contains important notes and considerations when using PowerMem. Please review these points to avoid common issues and ensure proper integration.

---

## Note 1: Memory ID Precision in JavaScript/TypeScript

### Overview

The `memory_id` field in PowerMem is a 64-bit Snowflake integer. When JSON responses containing this ID are parsed by JavaScript/TypeScript clients, the value may exceed JavaScript's 53-bit safe integer limit (`Number.MAX_SAFE_INTEGER = 2^53 - 1`) and lose precision.

### The Problem

JavaScript's `Number` type uses IEEE 754 double-precision floating-point format, which can only safely represent integers up to `2^53 - 1` (9,007,199,254,740,991). Any integer larger than this may lose precision when parsed from JSON.

### Example

When a memory is created and retrieved:

```python
from powermem import create_memory

memory = create_memory()
res = memory.get_all(user_id="hsy")
print(res)
```

The response contains a `memory_id` like `653247040368672768`. However, when this JSON is parsed in JavaScript/TypeScript:

```javascript
const response = JSON.parse('{"id": 653247040368672768, ...}');
console.log(response.id);  // Output: 653247040368672800 (precision lost!)
```

The value `653247040368672768` becomes `653247040368672800` due to precision loss.

### Solution

When working with JavaScript/TypeScript clients or any environment that may parse JSON, **treat `memory_id` as a string** rather than a number to preserve precision.

#### Python: Converting to String for JSON

When sending data to JavaScript/TypeScript clients, convert the `memory_id` to a string:

```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# Get memory - memory_id is returned as int in Python
result = memory.get_all(user_id="user123")
memory_id = result['results'][0]['id']  # int type

# When sending to JS/TS clients via JSON, convert to string
json_response = {
    "id": str(memory_id),  # Convert to string for JSON
    "memory": result['results'][0]['memory'],
    "user_id": result['results'][0]['user_id'],
    # ... other fields
}

# Or when using json.dumps with custom serializer
import json

def serialize_memory_id(obj):
    """Custom JSON serializer for memory_id"""
    if isinstance(obj, dict) and 'id' in obj:
        obj = obj.copy()
        obj['id'] = str(obj['id'])
    return obj

# Apply to all results
results = result.get('results', [])
for item in results:
    item['id'] = str(item['id'])

json_string = json.dumps(results)
```

#### JavaScript/TypeScript: Parsing as String

When receiving JSON responses, ensure `memory_id` is parsed as a string:

```typescript
// Option 1: Use a custom JSON reviver
const response = JSON.parse(jsonString, (key, value) => {
  if (key === 'id' && typeof value === 'number' && value > Number.MAX_SAFE_INTEGER) {
    return String(value);
  }
  return value;
});

// Option 2: Convert after parsing (if already lost precision, this won't help)
// Better to ensure the server sends it as a string
const response = JSON.parse(jsonString);
if (typeof response.id === 'number') {
  response.id = String(response.id);
}

// Option 3: Use a library that preserves big integers
// For example, using json-bigint
import JSONbig from 'json-bigint';
const response = JSONbig.parse(jsonString);
```

#### FastAPI Example

If you're building a FastAPI endpoint that returns memory data:

```python
from fastapi import FastAPI
from powermem import Memory, auto_config
import json

app = FastAPI()
config = auto_config()
memory = Memory(config=config)

@app.get("/memories")
async def get_memories(user_id: str):
    result = memory.get_all(user_id=user_id)
    
    # Convert memory_id to string for all results
    results = result.get('results', [])
    for item in results:
        item['id'] = str(item['id'])
    
    return {"results": results}
```

### Best Practices

1. **Always convert `memory_id` to string** when serializing to JSON for JavaScript/TypeScript clients
2. **Parse `memory_id` as string** in JavaScript/TypeScript when receiving JSON responses
3. **Use string type** for `memory_id` in API request/response schemas when targeting JS/TS clients
4. **Document this behavior** in your API documentation if you're building integrations

### Internal Storage

Note that internally, PowerMem continues to use 64-bit integers for `memory_id` storage and operations. The conversion to string is only necessary when:
- Serializing to JSON for external clients
- Passing `memory_id` through API endpoints
- Logging or displaying `memory_id` values in contexts where precision matters

### Related Issues

For more details and discussion, see [GitHub Issue #91](https://github.com/oceanbase/powermem/issues/91).

---
