"""
Memory Optimizer for compression and deduplication.

This module provides the MemoryOptimizer class which handles:
1. Exact match deduplication (hash-based)
2. Semantic deduplication (similarity-based) [Planned]
3. Memory compression using LLM summarization [Planned]
"""

import logging
from typing import Dict, Any, Optional, List
from collections import defaultdict
from powermem.prompts.optimization_prompts import MEMORY_COMPRESSION_PROMPT

logger = logging.getLogger(__name__)

class MemoryOptimizer:
    """
    Optimizer for memory storage to reduce redundancy and token usage.
    """

    def __init__(self, storage: Any, llm: Any):
        """
        Initialize the memory optimizer.

        Args:
            storage: The storage adapter instance
            llm: The LLM service instance
        """
        self.storage = storage
        self.llm = llm

    def deduplicate(
        self,
        user_id: Optional[str] = None,
        strategy: str = "exact",
        threshold: float = 0.95
    ) -> Dict[str, Any]:
        """
        Deduplicate memories based on the specified strategy.

        Args:
            user_id: Optional user ID to filter memories
            strategy: Deduplication strategy ("exact" or "semantic")
            threshold: Similarity threshold for semantic deduplication (0.0 to 1.0)

        Returns:
            Dict containing statistics about the operation:
            {
                "total_checked": int,
                "duplicates_found": int,
                "deleted_count": int,
                "errors": int
            }
        """
        if strategy == "exact":
            return self._deduplicate_exact(user_id)
        elif strategy == "semantic":
            return self._deduplicate_semantic(user_id, threshold)
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.

        Args:
            v1: First vector
            v2: Second vector

        Returns:
            Cosine similarity (float between -1.0 and 1.0)
        """
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0

        try:
            dot_product = sum(a * b for a, b in zip(v1, v2))
            magnitude1 = sum(a * a for a in v1) ** 0.5
            magnitude2 = sum(b * b for b in v2) ** 0.5

            if magnitude1 == 0 or magnitude2 == 0:
                return 0.0

            return dot_product / (magnitude1 * magnitude2)
        except Exception as e:
            logger.error(f"Error calculating cosine similarity: {e}")
            return 0.0

    def _deduplicate_semantic(self, user_id: Optional[str], threshold: float) -> Dict[str, Any]:
        """
        Perform semantic deduplication using vector similarity.

        Args:
            user_id: Optional user ID to filter memories
            threshold: Similarity threshold (0.0 to 1.0)

        Returns:
            Statistics dictionary
        """
        stats = {
            "total_checked": 0,
            "duplicates_found": 0,
            "deleted_count": 0,
            "errors": 0
        }

        try:
            # Fetch all memories
            memories = self.storage.get_all_memories(user_id=user_id, limit=1000)
            stats["total_checked"] = len(memories)

            # Filter memories that have embeddings
            memories_with_embeddings = [m for m in memories if m.get("embedding")]

            # Sort by creation time (oldest first) to preserve the original
            try:
                memories_with_embeddings.sort(key=lambda x: x.get("created_at") or x.get("id"))
            except Exception:
                memories_with_embeddings.sort(key=lambda x: x.get("id"))

            unique_memories = []
            duplicates = []

            for mem in memories_with_embeddings:
                is_duplicate = False
                current_embedding = mem.get("embedding")

                # Compare with already accepted unique memories
                # Note: This is O(N*M) where N is total memories and M is unique memories
                # For large datasets, we should use vector DB search or clustering
                for unique_mem in unique_memories:
                    similarity = self._cosine_similarity(current_embedding, unique_mem.get("embedding"))

                    if similarity >= threshold:
                        is_duplicate = True
                        break

                if is_duplicate:
                    duplicates.append(mem)
                else:
                    unique_memories.append(mem)

            stats["duplicates_found"] = len(duplicates)

            # Delete duplicates
            for dup in duplicates:
                try:
                    mem_id = dup.get("id")
                    if mem_id:
                        success = self.storage.delete_memory(mem_id, user_id=user_id)
                        if success:
                            stats["deleted_count"] += 1
                        else:
                            stats["errors"] += 1
                            logger.warning(f"Failed to delete duplicate memory ID: {mem_id}")
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Error deleting duplicate memory {dup.get('id')}: {e}")

        except Exception as e:
            logger.error(f"Error during semantic deduplication: {e}")
            stats["errors"] += 1

        return stats

    def compress(
        self,
        user_id: Optional[str] = None,
        threshold: float = 0.85
    ) -> Dict[str, Any]:
        """
        Compress memories by clustering similar ones and summarizing them.

        Args:
            user_id: Optional user ID to filter memories
            threshold: Similarity threshold for clustering (0.0 to 1.0)

        Returns:
            Statistics dictionary
        """
        stats = {
            "total_processed": 0,
            "clusters_found": 0,
            "compressed_count": 0,
            "new_memories_created": 0,
            "errors": 0
        }

        try:
            # 1. Fetch memories
            memories = self.storage.get_all_memories(user_id=user_id, limit=1000)
            valid_memories = [m for m in memories if m.get("embedding")]
            stats["total_processed"] = len(valid_memories)

            if not valid_memories:
                return stats

            # 2. Greedy Clustering
            clusters = []
            # Sort by ID to be deterministic
            valid_memories.sort(key=lambda x: x.get("id"))

            processed_ids = set()

            for i, mem in enumerate(valid_memories):
                if mem["id"] in processed_ids:
                    continue

                current_cluster = [mem]
                processed_ids.add(mem["id"])

                for j in range(i + 1, len(valid_memories)):
                    candidate = valid_memories[j]
                    if candidate["id"] in processed_ids:
                        continue

                    similarity = self._cosine_similarity(mem["embedding"], candidate["embedding"])
                    if similarity >= threshold:
                        current_cluster.append(candidate)
                        processed_ids.add(candidate["id"])

                if len(current_cluster) > 1:
                    clusters.append(current_cluster)

            stats["clusters_found"] = len(clusters)

            # 3. Process Clusters
            for cluster in clusters:
                try:
                    # Format prompt
                    memories_text = "\n".join([f"- {m.get('content') or m.get('memory')}" for m in cluster])
                    prompt = MEMORY_COMPRESSION_PROMPT.format(memories=memories_text)

                    # LLM Call
                    summary = self.llm.generate_response(messages=[{"role": "user", "content": prompt}])

                    if summary:
                        # Add new memory
                        self.storage.add_memory({
                            "content": summary,
                            "user_id": user_id,
                            "metadata": {"type": "compressed_summary", "source_count": len(cluster)}
                        })
                        stats["new_memories_created"] += 1

                        # Delete old memories
                        for old_mem in cluster:
                            self.storage.delete_memory(old_mem["id"], user_id=user_id)
                            stats["compressed_count"] += 1

                except Exception as e:
                    logger.error(f"Error processing cluster: {e}")
                    stats["errors"] += 1

        except Exception as e:
            logger.error(f"Error during compression: {e}")
            stats["errors"] += 1

        return stats

    def _deduplicate_exact(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Perform exact match deduplication using content hash.

        Args:
            user_id: Optional user ID to filter memories

        Returns:
            Statistics dictionary
        """
        stats = {
            "total_checked": 0,
            "duplicates_found": 0,
            "deleted_count": 0,
            "errors": 0
        }

        try:
            # Fetch all memories for the user
            # We use a large limit to catch as many as possible,
            # though in production pagination might be needed for very large datasets
            memories = self.storage.get_all_memories(user_id=user_id, limit=10000)
            stats["total_checked"] = len(memories)

            # Group by hash
            hash_groups = defaultdict(list)
            for mem in memories:
                # Use 'hash' field if available, otherwise compute it?
                # The storage adapter's get_all_memories returns a dict that might not have 'hash'
                # if it wasn't requested or stored. However, Memory.add stores a hash.
                # Let's check if 'hash' is present. If not, we can fall back to hashing content.
                content_hash = mem.get("hash")
                if not content_hash:
                    # Fallback if hash not present in record
                    import hashlib
                    content = mem.get("memory", "") or mem.get("content", "")
                    if content:
                        content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

                if content_hash:
                    hash_groups[content_hash].append(mem)

            # Identify and delete duplicates
            for content_hash, group in hash_groups.items():
                if len(group) > 1:
                    # Sort by creation time (or ID), keep the oldest (first created)
                    # Assuming 'created_at' is ISO string or datetime
                    # If not available, use ID

                    # Sort logic:
                    # 1. Try sorting by created_at
                    # 2. Fallback to ID
                    try:
                        group.sort(key=lambda x: x.get("created_at") or x.get("id"))
                    except Exception:
                        # Fallback sort by ID if types don't match or created_at is missing
                        group.sort(key=lambda x: x.get("id"))

                    # The first one is the "original" (oldest), others are duplicates
                    duplicates = group[1:]
                    stats["duplicates_found"] += len(duplicates)

                    for dup in duplicates:
                        try:
                            mem_id = dup.get("id")
                            if mem_id:
                                success = self.storage.delete_memory(mem_id, user_id=user_id)
                                if success:
                                    stats["deleted_count"] += 1
                                else:
                                    stats["errors"] += 1
                                    logger.warning(f"Failed to delete duplicate memory ID: {mem_id}")
                        except Exception as e:
                            stats["errors"] += 1
                            logger.error(f"Error deleting duplicate memory {dup.get('id')}: {e}")

        except Exception as e:
            logger.error(f"Error during exact deduplication: {e}")
            stats["errors"] += 1

        return stats
