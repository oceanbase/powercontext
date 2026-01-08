"""
Search service for PowerMem API
"""

import logging
from typing import Any, Dict, List, Optional
from powermem import Memory, auto_config
from ..models.errors import ErrorCode, APIError
from ..utils.metrics import get_metrics_collector

logger = logging.getLogger("server")


class SearchService:
    """Service for memory search operations"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize search service.
        
        Args:
            config: PowerMem configuration (uses auto_config if None)
        """
        if config is None:
            config = auto_config()
        
        self.memory = Memory(config=config)
        logger.info("SearchService initialized")
    
    def search_memories(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
    ) -> Dict[str, Any]:
        """
        Search memories.
        
        Args:
            query: Search query
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            filters: Additional filters
            limit: Maximum number of results
            
        Returns:
            Search results dictionary
            
        Raises:
            APIError: If search fails
        """
        try:
            if not query or not query.strip():
                raise APIError(
                    code=ErrorCode.INVALID_SEARCH_PARAMS,
                    message="Search query cannot be empty",
                    status_code=400,
                )
            
            results = self.memory.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
                limit=limit,
            )
            
            logger.info(f"Search completed: {len(results.get('results', []))} results")
            
            # Record successful memory operation
            metrics_collector = get_metrics_collector()
            metrics_collector.record_memory_operation("search", "success")
            
            return results
            
        except APIError:
            # Record failed memory operation for API errors
            metrics_collector = get_metrics_collector()
            metrics_collector.record_memory_operation("search", "failed")
            raise
        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            
            # Record failed memory operation
            metrics_collector = get_metrics_collector()
            metrics_collector.record_memory_operation("search", "failed")
            
            raise APIError(
                code=ErrorCode.SEARCH_FAILED,
                message=f"Search failed: {str(e)}",
                status_code=500,
            )
