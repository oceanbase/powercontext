"""
Abstract base class for memory management

This module defines the core interface that all memory implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from datetime import datetime


class MemoryBase(ABC):
    """
    Abstract base class for memory management.
    
    This class defines the core interface that all memory implementations must implement.
    """
    
    @abstractmethod
    def add(
        self,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Add a new memory.
        
        Args:
            content: The memory content to store
            user_id: ID of the user creating the memory
            agent_id: ID of the agent creating the memory
            run_id: ID of the run/session
            metadata: Additional metadata for the memory
            filters: Additional filters for the memory
            
        Returns:
            Dictionary containing the added memory information
        """
        pass
    
    @abstractmethod
    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 30,
    ) -> Dict[str, Any]:
        """
        Search for memories.
        
        Args:
            query: Search query
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            filters: Additional filters
            limit: Maximum number of results
            
        Returns:
            Dictionary containing search results in format: {"results": [...]}
        """
        pass
    
    @abstractmethod
    def get(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific memory by ID.
        
        Args:
            memory_id: ID of the memory to retrieve
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            Memory data if found, None otherwise
        """
        pass
    
    @abstractmethod
    def update(
        self,
        memory_id: int,
        content: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing memory.
        
        Args:
            memory_id: ID of the memory to update
            content: New content for the memory
            user_id: User ID for access control
            agent_id: Agent ID for access control
            metadata: Updated metadata
            
        Returns:
            Dictionary containing the updated memory information
        """
        pass
    
    @abstractmethod
    def delete(
        self,
        memory_id: int,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Delete a memory.
        
        Args:
            memory_id: ID of the memory to delete
            user_id: User ID for access control
            agent_id: Agent ID for access control
            
        Returns:
            True if deleted successfully, False otherwise
        """
        pass
    
    @abstractmethod
    def get_all(
        self,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: Optional[str] = None,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """
        Get all memories with optional filtering.
        
        Args:
            user_id: Filter by user ID
            agent_id: Filter by agent ID
            run_id: Filter by run ID
            limit: Maximum number of results
            offset: Number of results to skip
            filters: Additional filters
            sort_by: Field to sort by
            order: Sort order
            
        Returns:
            List of memories
        """
        pass
    
    @abstractmethod
    def reset(self) -> None:
        """
        Reset the memory store by:
            Deletes the vector store collection
            Resets the database
            Recreates the vector store with a new client
        """
        pass
