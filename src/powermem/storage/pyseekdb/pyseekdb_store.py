import logging
from typing import Any, Dict, List, Optional

from powermem.storage.base import OutputData, VectorStoreBase

try:
    import pyseekdb
except ImportError:
    raise ImportError("Please install pyseekdb: pip install pyseekdb")

logger = logging.getLogger(__name__)


class PySeekDBVectorStore(VectorStoreBase):
    """PySeekDB vector store implementation"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        db_name: str,
        collection_name: str = "powermem",
        dimension: int = 1536,
        metric_type: str = "l2",
        **kwargs: Any,
    ):
        self.client = pyseekdb.Client(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=db_name,
        )
        self.collection_name = collection_name
        self.dimension = dimension
        self.metric_type = metric_type

        # Try to get existing collection, or create if not exists
        try:
            self.collection = self.client.get_collection(self.collection_name)
        except Exception:
            # If collection doesn't exist, create it with default params
            self.collection = self.client.create_collection(
                name=self.collection_name,
                dimension=self.dimension,
                metric=self.metric_type,
            )

    def create_col(self, name: str, vector_size: int, distance: str) -> Any:
        """Create a new collection."""
        return self.client.create_collection(
            name=name,
            dimension=vector_size,
            metric=distance,
        )

    def insert(
        self,
        vectors: List[List[float]],
        payloads: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[int]] = None
    ) -> None:
        """Insert vectors into a collection."""
        if self.collection is None:
            raise ValueError("Collection not initialized")

        # Convert IDs to string if they are provided, as pyseekdb might expect specific formats
        # But we keep them as is if pyseekdb supports int IDs.
        # Assuming ids are required or optional.

        self.collection.add(
            embeddings=vectors,
            metadatas=payloads,  # type: ignore
            ids=ids  # type: ignore
        )

    def search(
        self,
        query: List[float],
        vectors: Optional[List[List[float]]] = None,
        limit: int = 5,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[OutputData]:
        """Search for similar vectors."""
        if self.collection is None:
            raise ValueError("Collection not initialized")

        # pyseekdb query format
        results = self.collection.query(
            query_embeddings=[query],
            n_results=limit,
            where=filters  # type: ignore
        )

        # Convert results to OutputData
        output = []
        if not results or 'ids' not in results or not results['ids']:
            return []

        # results structure is dict of lists (batch result)
        ids_list = results['ids'][0]
        distances_list = results['distances'][0] if 'distances' in results and results['distances'] else []
        metadatas_list = results['metadatas'][0] if 'metadatas' in results and results['metadatas'] else []

        for i, id_val in enumerate(ids_list):
            score_val = distances_list[i] if i < len(distances_list) else 0.0
            payload_val = metadatas_list[i] if i < len(metadatas_list) else {}

            output.append(OutputData(
                id=int(id_val) if isinstance(id_val, (int, str)) and str(id_val).isdigit() else 0,
                score=float(score_val),
                payload=payload_val  # type: ignore
            ))

        return output

    def delete(self, vector_id: int) -> None:
        """Delete a vector by ID."""
        if self.collection is None:
            raise ValueError("Collection not initialized")
        self.collection.delete(ids=[vector_id])  # type: ignore

    def update(
        self,
        vector_id: int,
        vector: Optional[List[float]] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> None:
        """Update a vector and its payload."""
        if self.collection is None:
            raise ValueError("Collection not initialized")

        kwargs: Dict[str, Any] = {"ids": [vector_id]}
        if vector is not None:
            kwargs["embeddings"] = [vector]
        if payload is not None:
            kwargs["metadatas"] = [payload]

        self.collection.update(**kwargs)

    def get(self, vector_id: int) -> Optional[OutputData]:
        """Retrieve a vector by ID."""
        if self.collection is None:
            raise ValueError("Collection not initialized")

        result = self.collection.get(ids=[vector_id], include=["metadatas", "embeddings"]) # type: ignore
        if not result or 'ids' not in result or not result['ids']:
            return None

        # Assuming single result for single ID
        payload = result['metadatas'][0] if result.get('metadatas') else {} # type: ignore

        return OutputData(
            id=vector_id,
            score=0.0,
            payload=payload # type: ignore
        )

    def list_cols(self) -> List[str]:
        """List all collections."""
        # pyseekdb likely returns a list of collection objects or names
        cols = self.client.list_collections()
        # If cols contains objects, map to names; if strings, return as is
        return [c.name if hasattr(c, 'name') else str(c) for c in cols]

    def delete_col(self) -> None:
        """Delete the current collection."""
        if self.collection_name:
            self.client.delete_collection(self.collection_name)
            self.collection = None

    def col_info(self) -> Dict[str, Any]:
        """Get information about a collection."""
        if self.collection:
            return {
                "name": self.collection.name,
                "count": self.collection.count()
            }
        return {}

    def list(self, filters: Optional[Dict[str, Any]] = None, limit: Optional[int] = None) -> List[OutputData]:
        """List all memories."""
        if self.collection is None:
            raise ValueError("Collection not initialized")

        res = self.collection.get(where=filters, limit=limit) # type: ignore

        output = []
        if res and 'ids' in res and res['ids']:
            ids = res['ids']
            metadatas = res.get('metadatas', [])

            for i, id_val in enumerate(ids):
                payload = metadatas[i] if metadatas and i < len(metadatas) else {} # type: ignore
                output.append(OutputData(
                    id=int(id_val) if isinstance(id_val, (int, str)) and str(id_val).isdigit() else 0,
                    score=0.0,
                    payload=payload # type: ignore
                ))
        return output

    def reset(self) -> None:
        """Reset by delete the collection and recreate it."""
        try:
            self.delete_col()
        except Exception:
            pass # Ignore if doesn't exist

        self.collection = self.client.create_collection(
            name=self.collection_name,
            dimension=self.dimension,
            metric=self.metric_type
        )

    def get_statistics(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get statistics for the memories."""
        if self.collection is None:
            return {"count": 0}

        if filters:
            # If filtered count is needed
            res = self.collection.get(where=filters, include=[]) # type: ignore
            count = len(res['ids']) if res and 'ids' in res else 0
            return {"count": count}

        return {"count": self.collection.count()}

    def get_unique_users(self) -> List[str]:
        """Get a list of unique user IDs."""
        if self.collection is None:
            return []

        res = self.collection.get(include=["metadatas"]) # type: ignore
        users = set()
        if res and 'metadatas' in res and res['metadatas']:
            for meta in res['metadatas']:
                if meta and isinstance(meta, dict) and 'user_id' in meta:
                    users.add(str(meta['user_id']))
        return list(users)
