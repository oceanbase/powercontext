# Memory Compression and Deduplication Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Implement an intelligent system to optimize memory storage by deduplicating redundant records and compressing related memories using LLM summarization.

**Architecture:**
- **New Component:** `MemoryOptimizer` class in `src/powermem/intelligence/memory_optimizer.py` encapsulates all optimization logic.
- **Integration:** The main `Memory` class delegates optimization requests to `MemoryOptimizer`.
- **Strategies:**
  - **Deduplication:** Hybrid approach using Exact Match (Hash) and Semantic Match (Vector Similarity).
  - **Compression:** LLM-based summarization of clustered memories.

**Tech Stack:** Python, Pytest, LLM (via existing factory), Vector Store (existing).

---

### Task 1: Skeleton & Exact Match Deduplication

**Files:**
- Create: `src/powermem/intelligence/memory_optimizer.py`
- Test: `tests/unit/intelligence/test_memory_optimizer.py`

**Step 1: Write the failing test**

Create `tests/unit/intelligence/test_memory_optimizer.py`:

```python
import pytest
from unittest.mock import MagicMock
from powermem.intelligence.memory_optimizer import MemoryOptimizer

@pytest.fixture
def optimizer():
    mock_storage = MagicMock()
    mock_llm = MagicMock()
    return MemoryOptimizer(storage=mock_storage, llm=mock_llm)

def test_deduplicate_exact_match(optimizer):
    # Setup mock data: 2 records with same hash
    records = [
        {"id": 1, "content": "hello world", "hash": "abc", "metadata": {}},
        {"id": 2, "content": "hello world", "hash": "abc", "metadata": {}},
        {"id": 3, "content": "unique", "hash": "xyz", "metadata": {}},
    ]

    # Mock storage.get_all_memories to return these records
    optimizer.storage.get_all_memories.return_value = records

    # Run deduplication
    result = optimizer.deduplicate(strategy="exact")

    # Verify results
    assert result["duplicates_found"] == 1
    assert result["merged"] == 1

    # Verify delete was called for the duplicate (id 2)
    optimizer.storage.delete_memory.assert_called_with(2)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/intelligence/test_memory_optimizer.py -v`
Expected: FAIL (ImportError or attributes missing)

**Step 3: Write minimal implementation**

Create `src/powermem/intelligence/memory_optimizer.py`:

```python
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class MemoryOptimizer:
    def __init__(self, storage, llm):
        self.storage = storage
        self.llm = llm

    def deduplicate(self, user_id: str = None, strategy: str = "exact", threshold: float = 0.95) -> Dict[str, Any]:
        """
        Deduplicate memories based on strategy.
        Strategies: "exact" (hash-based), "semantic" (vector-based, TODO)
        """
        stats = {"duplicates_found": 0, "merged": 0}

        # Get all memories for analysis
        # Note: In production this should be paginated or batched,
        # but for initial implementation we fetch all for analysis
        memories = self.storage.get_all_memories(user_id=user_id, limit=1000)

        if strategy == "exact":
            seen_hashes = {}
            for mem in memories:
                mem_hash = mem.get("hash")
                mem_id = mem.get("id")

                if mem_hash in seen_hashes:
                    # Found duplicate
                    logger.info(f"Found exact duplicate: {mem_id} (duplicate of {seen_hashes[mem_hash]})")
                    self.storage.delete_memory(mem_id)
                    stats["duplicates_found"] += 1
                    stats["merged"] += 1
                else:
                    seen_hashes[mem_hash] = mem_id

        return stats
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/intelligence/test_memory_optimizer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/powermem/intelligence/memory_optimizer.py tests/unit/intelligence/test_memory_optimizer.py
git commit -m "feat: implement basic MemoryOptimizer with exact match deduplication"
```

---

### Task 2: Semantic Deduplication

**Files:**
- Modify: `src/powermem/intelligence/memory_optimizer.py`
- Test: `tests/unit/intelligence/test_memory_optimizer.py`

**Step 1: Write the failing test**

Update `tests/unit/intelligence/test_memory_optimizer.py` adding `test_deduplicate_semantic`:

```python
def test_deduplicate_semantic(optimizer):
    # Setup: 2 semantically similar memories
    mem1 = {"id": 1, "content": "The sky is blue", "embedding": [0.1, 0.2]}
    mem2 = {"id": 2, "content": "The sky has a blue color", "embedding": [0.11, 0.21]}

    # Mock storage to return memories
    optimizer.storage.get_all_memories.return_value = [mem1, mem2]

    # Mock search to find similar items when analyzing mem1
    # Note: real implementation might iterate and search, or use O(N^2) comparison for small batches
    # Let's assume the implementation iterates and checks similarity

    # Mock vector store similarity check if we implement it manually,
    # OR mock storage.search_memories if we use that.
    # Let's assume we use a helper method _calculate_similarity or rely on storage search.
    pass
    # (Since I cannot easily mock complex vector math without numpy/scikit logic in the test,
    #  I will test that it calls the deletion if similarity is detected)
```

**Alternative Test Strategy:**
Since implementing vector math in tests is brittle, we'll verify the logic flow.

```python
def test_deduplicate_semantic_flow(optimizer):
    # We will simulate a scenario where we find a duplicate via semantic search
    optimizer.storage.get_all_memories.return_value = [
        {"id": 1, "content": "A", "embedding": [1.0]},
        {"id": 2, "content": "B", "embedding": [0.9]} # Close enough
    ]

    # We'll patch the similarity function or logic
    # For this plan, let's assume we implement a simple cosine similarity helper

    result = optimizer.deduplicate(strategy="semantic", threshold=0.9)
    # This is tricky to test without implementation details.
    # Let's write the test after we decide the implementation in Step 3.
```

**Refined Step 1: Write failing test**

```python
def test_calculate_similarity():
    # Test internal helper
    vec1 = [1.0, 0.0]
    vec2 = [1.0, 0.0]
    vec3 = [0.0, 1.0]

    assert MemoryOptimizer._cosine_similarity(vec1, vec2) > 0.99
    assert MemoryOptimizer._cosine_similarity(vec1, vec3) < 0.1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/intelligence/test_memory_optimizer.py::test_calculate_similarity -v`
Expected: FAIL (AttributeError)

**Step 3: Implement Semantic Logic**

Modify `src/powermem/intelligence/memory_optimizer.py`:

```python
import math

class MemoryOptimizer:
    # ... existing init ...

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        if not v1 or not v2: return 0.0
        dot_product = sum(a*b for a,b in zip(v1, v2))
        norm_a = math.sqrt(sum(a*a for a in v1))
        norm_b = math.sqrt(sum(b*b for b in v2))
        return dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0

    def deduplicate(self, user_id: str = None, strategy: str = "exact", threshold: float = 0.95) -> Dict[str, Any]:
        # ... existing exact logic ...

        if strategy == "semantic":
            memories = self.storage.get_all_memories(user_id=user_id, limit=100) # Limit for safety
            processed_ids = set()

            for i, mem1 in enumerate(memories):
                id1 = mem1.get("id")
                if id1 in processed_ids: continue

                vec1 = mem1.get("embedding")
                if not vec1: continue

                # Compare with subsequent memories
                for j in range(i + 1, len(memories)):
                    mem2 = memories[j]
                    id2 = mem2.get("id")
                    if id2 in processed_ids: continue

                    vec2 = mem2.get("embedding")
                    if not vec2: continue

                    similarity = self._cosine_similarity(vec1, vec2)
                    if similarity >= threshold:
                        # Merge/Delete mem2
                        logger.info(f"Found semantic duplicate: {id2} similar to {id1} (score: {similarity})")
                        self.storage.delete_memory(id2)
                        processed_ids.add(id2)
                        stats["duplicates_found"] += 1
                        stats["merged"] += 1

        return stats
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/intelligence/test_memory_optimizer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/powermem/intelligence/memory_optimizer.py tests/unit/intelligence/test_memory_optimizer.py
git commit -m "feat: add semantic deduplication logic"
```

---

### Task 3: Compression with LLM

**Files:**
- Create: `src/powermem/prompts/optimization_prompts.py`
- Modify: `src/powermem/intelligence/memory_optimizer.py`
- Test: `tests/unit/intelligence/test_memory_optimizer.py`

**Step 1: Write the failing test**

Update `tests/unit/intelligence/test_memory_optimizer.py`:

```python
def test_compress_logic(optimizer):
    # Mock LLM response
    optimizer.llm.generate_response.return_value = "Compressed content"

    # Mock storage memories
    optimizer.storage.get_all_memories.return_value = [
        {"id": 1, "content": "Part 1", "embedding": [1.0]},
        {"id": 2, "content": "Part 2", "embedding": [1.0]} # Similar enough to cluster
    ]

    result = optimizer.compress(user_id="u1", threshold=0.9)

    # Verify LLM called
    assert optimizer.llm.generate_response.called
    # Verify old memories deleted
    assert optimizer.storage.delete_memory.call_count == 2
    # Verify new memory added
    assert optimizer.storage.add_memory.called
    assert result["compressed"] > 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/intelligence/test_memory_optimizer.py::test_compress_logic -v`
Expected: FAIL (AttributeError)

**Step 3: Implement Compression**

1. Create `src/powermem/prompts/optimization_prompts.py`:
```python
MEMORY_COMPRESSION_PROMPT = """
You are a memory optimization assistant.
Combine the following related memories into a single, concise, and comprehensive memory record.
Preserve all key facts, dates, and entities.
Remove redundancy.

Memories to compress:
{memories}

Compressed Memory:
"""
```

2. Modify `src/powermem/intelligence/memory_optimizer.py`:
```python
from powermem.prompts.optimization_prompts import MEMORY_COMPRESSION_PROMPT

class MemoryOptimizer:
    # ... existing code ...

    def compress(self, user_id: str = None, threshold: float = 0.85) -> Dict[str, Any]:
        stats = {"compressed": 0, "original_count": 0, "new_count": 0}

        # 1. Fetch memories
        memories = self.storage.get_all_memories(user_id=user_id, limit=100)
        stats["original_count"] = len(memories)

        # 2. Cluster memories (Naive approach: Greedy clustering)
        clusters = []
        visited = set()

        for i, mem in enumerate(memories):
            if mem["id"] in visited: continue

            current_cluster = [mem]
            visited.add(mem["id"])
            vec1 = mem.get("embedding")

            if vec1:
                for j in range(i+1, len(memories)):
                    other = memories[j]
                    if other["id"] in visited: continue

                    vec2 = other.get("embedding")
                    if vec2 and self._cosine_similarity(vec1, vec2) >= threshold:
                        current_cluster.append(other)
                        visited.add(other["id"])

            if len(current_cluster) > 1:
                clusters.append(current_cluster)

        # 3. Compress Clusters
        for cluster in clusters:
            memory_texts = [m.get("content", "") for m in cluster]
            prompt = MEMORY_COMPRESSION_PROMPT.format(memories="\n- ".join(memory_texts))

            try:
                # Generate summary
                compressed_content = self.llm.generate_response(
                    messages=[{"role": "user", "content": prompt}]
                )

                # Add new memory
                self.storage.add_memory({
                    "content": compressed_content,
                    "user_id": user_id,
                    "metadata": {"type": "compressed_memory", "source_count": len(cluster)}
                })

                # Delete old memories
                for mem in cluster:
                    self.storage.delete_memory(mem["id"])

                stats["compressed"] += 1

            except Exception as e:
                logger.error(f"Compression failed for cluster: {e}")

        stats["new_count"] = stats["original_count"] - sum(len(c) for c in clusters) + len(clusters)
        return stats
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/intelligence/test_memory_optimizer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/powermem/prompts/optimization_prompts.py src/powermem/intelligence/memory_optimizer.py tests/unit/intelligence/test_memory_optimizer.py
git commit -m "feat: implement memory compression using LLM"
```

---

### Task 4: Integrate into Memory Core

**Files:**
- Modify: `src/powermem/core/memory.py`
- Modify: `src/powermem/core/memory.py` (imports)

**Step 1: Write failing test**

We need to test `Memory.optimize()`. Since `Memory` is a large class, we'll assume we can add a test case in `tests/unit/core/test_memory.py` (if it exists) or create a new test file `tests/unit/core/test_memory_optimize.py`.

```python
import pytest
from unittest.mock import MagicMock
from powermem.core.memory import Memory

def test_memory_optimize_delegation():
    # Setup
    mem = Memory(config={"llm": {"provider": "mock"}, "vector_store": {"provider": "mock"}})

    # Mock internal optimizer
    mem.optimizer = MagicMock()

    # Call
    mem.optimize(strategy="deduplicate")

    # Verify
    mem.optimizer.deduplicate.assert_called()
```

**Step 2: Run test**

Run: `pytest tests/unit/core/test_memory_optimize.py -v`
Expected: FAIL (Method not found)

**Step 3: Modify Memory Class**

In `src/powermem/core/memory.py`:

1. Import:
```python
from ..intelligence.memory_optimizer import MemoryOptimizer
```

2. Init:
```python
    def __init__(self, ...):
        # ... existing code ...
        self.optimizer = MemoryOptimizer(self.storage, self.llm)
```

3. Add Method:
```python
    def optimize(self, strategy: str = "deduplicate", **kwargs) -> Dict[str, Any]:
        """
        Optimize memory storage.

        Args:
            strategy: "deduplicate" or "compress"
            **kwargs: Additional args like threshold, user_id

        Returns:
            Optimization stats
        """
        if strategy == "deduplicate":
            # Extract specific args
            sub_strategy = kwargs.get("dedup_strategy", "exact")
            return self.optimizer.deduplicate(
                user_id=kwargs.get("user_id"),
                strategy=sub_strategy,
                threshold=kwargs.get("threshold", 0.95)
            )
        elif strategy == "compress":
            return self.optimizer.compress(
                user_id=kwargs.get("user_id"),
                threshold=kwargs.get("threshold", 0.85)
            )
        else:
            raise ValueError(f"Unknown optimization strategy: {strategy}")
```

**Step 4: Run test**

Run: `pytest tests/unit/core/test_memory_optimize.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/powermem/core/memory.py tests/unit/core/test_memory_optimize.py
git commit -m "feat: expose optimize method in Memory class"
```
