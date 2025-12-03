"""
Pluggable intelligent memory plugin interface and default implementation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from powermem.utils.utils import get_current_datetime
from typing import Any, Dict, List, Optional, Tuple

from .importance_evaluator import ImportanceEvaluator
from .ebbinghaus_algorithm import EbbinghausAlgorithm


logger = logging.getLogger(__name__)


class IntelligentMemoryPlugin:
    """
    Interface for intelligent memory plugins.
    Implementations can annotate new memories and manage lifecycle on access/search.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}

    @property
    def enabled(self) -> bool:  # pragma: no cover - trivial
        return bool(self.config.get("enabled", False))

    def on_add(self, *, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Hook invoked before persisting a memory. Return extra fields to merge into record.
        """
        return {}

    def on_get(self, memory: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Hook invoked on single memory access.
        Returns (updates, delete_flag). If delete_flag is True, caller should delete memory.
        """
        return None, False

    def on_search(self, results: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
        """
        Hook invoked on batch search results.
        Returns (updates, delete_ids).
        updates: list of (memory_id, update_dict)
        delete_ids: list of memory ids to delete
        """
        return [], []


class EbbinghausIntelligencePlugin(IntelligentMemoryPlugin):
    """
    Default plugin implementing importance evaluation and lifecycle with Ebbinghaus.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._importance = None
        self._algo = None
        if self.enabled:
            try:
                # Prepare importance config, merging custom_importance_evaluation_prompt if present
                importance_config = self.config.get("importance", {})
                if "custom_importance_evaluation_prompt" in self.config:
                    importance_config = importance_config.copy() if isinstance(importance_config, dict) else {}
                    importance_config["custom_importance_evaluation_prompt"] = self.config["custom_importance_evaluation_prompt"]
                
                self._importance = ImportanceEvaluator(
                    importance_config,
                    self.config.get("llm", {}),
                )
                # Support both "ebbinghaus" and direct intelligent_memory config
                ebbinghaus_cfg = self.config.get("ebbinghaus", {})
                # If no ebbinghaus config, use the main config as ebbinghaus config
                if not ebbinghaus_cfg:
                    ebbinghaus_cfg = self.config
                self._algo = EbbinghausAlgorithm(ebbinghaus_cfg)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Failed to init Ebbinghaus plugin: {e}")
                self.config["enabled"] = False

    def _classify(self, score: float) -> str:
        if not self._algo:
            return "working"
        if score >= self._algo.long_term_threshold:
            return "long_term"
        if score >= self._algo.short_term_threshold:
            return "short_term"
        return "working"

    def on_add(self, *, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.enabled or not self._importance or not self._algo:
            return {}
        try:
            # Evaluate importance
            score = self._importance.evaluate_importance(content, metadata)
            
            # Classify memory type
            memory_type = self._classify(score)
            
            # Process with Ebbinghaus algorithm to get intelligence metadata
            intelligence_metadata = self._algo.process_memory_metadata(content, score, memory_type)
            
            # Return enhanced metadata
            return {
                "importance_score": score,
                "memory_type": memory_type,
                "access_count": 0,
                "intelligence": intelligence_metadata.get('intelligence', {}),
                "memory_management": intelligence_metadata.get('memory_management', {}),
                "processing_applied": True,
            }
        except Exception as e:
            logger.warning(f"Failed to process memory in on_add: {e}")
            return {"access_count": 0}

    def on_get(self, memory: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
        if not self.enabled or not self._algo:
            return None, False
        try:
            updates: Dict[str, Any] = {
                "access_count": (memory.get("access_count") or 0) + 1,
                "updated_at": get_current_datetime(),
            }
            
            # Check if memory should be forgotten
            if self._algo.should_forget(memory):
                return None, True
            
            # Check if memory should be promoted
            if self._algo.should_promote(memory):
                current = memory.get("memory_type")
                if current == "working":
                    updates["memory_type"] = "short_term"
                elif current == "short_term":
                    updates["memory_type"] = "long_term"
            
            # Check if memory should be archived
            if self._algo.should_archive(memory):
                meta = memory.get("metadata") or {}
                meta["archived"] = True
                updates["metadata"] = meta
            
            # Re-process content if memory type changed or if it's been accessed multiple times
            access_count = memory.get("access_count", 0) + 1
            if (updates.get("memory_type") != memory.get("memory_type") or 
                access_count % 5 == 0):  # Re-process every 5 accesses
                
                original_content = memory.get("original_content") or memory.get("content", "")
                importance_score = memory.get("importance_score", 0.5)
                memory_type = updates.get("memory_type") or memory.get("memory_type", "working")
                
                # Re-process with updated parameters
                intelligence_metadata = self._algo.process_memory_metadata(original_content, importance_score, memory_type)
                updates.update(intelligence_metadata)
                updates["last_reprocessed_at"] = get_current_datetime()
            
            return updates, False
        except Exception as e:
            logger.warning(f"Failed to process memory in on_get: {e}")
            return None, False

    def on_search(self, results: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
        if not self.enabled or not self._algo:
            return [], []
        updates: List[Tuple[str, Dict[str, Any]]] = []
        deletes: List[str] = []
        
        for item in results:
            try:
                mem_id = item.get("id") or item.get("memory_id")
                if not mem_id:
                    continue
                
                # Process individual memory
                upd, delete_flag = self.on_get(item)
                
                if delete_flag:
                    deletes.append(mem_id)
                elif upd:
                    # Add search-specific enhancements
                    search_updates = self._enhance_for_search(item, upd)
                    updates.append((mem_id, search_updates))
                    
            except Exception as e:
                logger.warning(f"Failed to process memory {mem_id} in on_search: {e}")
                continue
                
        return updates, deletes
    
    def _enhance_for_search(self, memory: Dict[str, Any], base_updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhance memory for search context.
        
        Args:
            memory: Memory data
            base_updates: Base updates from on_get
            
        Returns:
            Enhanced updates for search context
        """
        try:
            # Add search-specific metadata
            search_metadata = memory.get("metadata", {})
            search_metadata["last_searched_at"] = get_current_datetime()
            search_metadata["search_count"] = search_metadata.get("search_count", 0) + 1
            
            # Update base updates with search metadata
            enhanced_updates = base_updates.copy()
            enhanced_updates["metadata"] = search_metadata
            
            # Add search relevance score if not present
            if "search_relevance_score" not in memory:
                # Simple relevance calculation based on access patterns
                access_count = memory.get("access_count", 0)
                importance_score = memory.get("importance_score", 0.5)
                search_relevance = min(1.0, (access_count * 0.1) + (importance_score * 0.5))
                enhanced_updates["search_relevance_score"] = search_relevance
            
            return enhanced_updates
            
        except Exception as e:
            logger.warning(f"Failed to enhance memory for search: {e}")
            return base_updates


