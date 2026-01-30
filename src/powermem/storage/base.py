"""
Abstract base class for storage implementations

This module defines the storage interface that all implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Any, List

from pydantic import BaseModel


class OutputData(BaseModel):
    id: Optional[int]  # memory id (Snowflake ID - 64-bit integer)
    score: Optional[float]  # distance
    payload: Optional[Dict]  # metadata

class VectorStoreBase(ABC):
    """
    Abstract base class for storage implementations.
    
    This class defines the interface that all storage backends must implement.
    """

    @abstractmethod
    def create_col(self, name, vector_size, distance):
        """Create a new collection."""
        pass

    @abstractmethod
    def insert(self, vectors, payloads=None, ids=None):
        """Insert vectors into a collection."""
        pass

    @abstractmethod
    def search(self, query, vectors, limit=5, filters=None):
        """Search for similar vectors."""
        pass

    @abstractmethod
    def delete(self, vector_id):
        """Delete a vector by ID."""
        pass

    @abstractmethod
    def update(self, vector_id, vector=None, payload=None):
        """Update a vector and its payload."""
        pass

    @abstractmethod
    def get(self, vector_id):
        """Retrieve a vector by ID."""
        pass

    @abstractmethod
    def list_cols(self):
        """List all collections."""
        pass

    @abstractmethod
    def delete_col(self):
        """Delete a collection."""
        pass

    @abstractmethod
    def col_info(self):
        """Get information about a collection."""
        pass

    @abstractmethod
    def list(self, filters=None, limit=None):
        """List all memories."""
        pass

    @abstractmethod
    def reset(self):
        """Reset by delete the collection and recreate it."""
        pass

    @abstractmethod
    def get_statistics(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get statistics for the memories."""
        pass

    @abstractmethod
    def get_unique_users(self) -> List[str]:
        """Get a list of unique user IDs."""
        pass


class GraphStoreBase(ABC):
    """
    Abstract base class for graph storage implementations.

    This class defines the interface that all graph storage backends must implement.
    """
    @abstractmethod
    def add(self, data: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Add data to the graph."""
        pass

    @abstractmethod
    def search(self, query: str, filters: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
        """Search for memories."""
        pass

    @abstractmethod
    def delete_all(self, filters: Dict[str, Any]) -> None:
        """Delete all graph data for the given filters."""
        pass

    @abstractmethod
    def get_all(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, str]]:
        """Retrieve all nodes and relationships from the graph database."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the graph by clearing all nodes and relationships."""
        pass

    @abstractmethod
    def get_statistics(
        self, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get statistics for the graph data."""
        pass

    @abstractmethod
    def get_unique_users(self) -> List[str]:
        """Get a list of unique user IDs."""
        pass
