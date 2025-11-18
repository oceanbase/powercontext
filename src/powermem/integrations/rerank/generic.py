"""
Generic Rerank implementation for unified rerank API services

This implementation supports any rerank service that follows the standard rerank API format,
such as Xinference, Jina-compatible services, and other unified rerank platforms.
"""
import os
from typing import List, Optional, Tuple

try:
    import httpx
except ImportError:
    httpx = None

from powermem.integrations.rerank.base import RerankBase
from powermem.integrations.rerank.config.base import BaseRerankConfig


class GenericRerank(RerankBase):
    """Generic Rerank implementation for unified rerank API services
    
    This implementation supports any rerank service that follows the standard rerank API format.
    It can be used with unified model inference platforms like Xinference, or any other
    service that provides a compatible rerank API endpoint.
    
    The API should accept POST requests with the following format:
    {
        "model": "model-name-or-uid",
        "query": "search query",
        "documents": ["doc1", "doc2", ...]
    }
    
    And return responses in the format:
    {
        "results": [
            {"index": 0, "relevance_score": 0.95},
            {"index": 1, "relevance_score": 0.87},
            ...
        ]
    }
    
    Args:
        config (Optional[BaseRerankConfig]): Configuration for the rerank model
            - api_base_url: Rerank service API endpoint URL (required)
            - model: Model name or UID (required)
            - api_key: Optional API key for authentication
    """

    def __init__(self, config: Optional[BaseRerankConfig] = None):
        super().__init__(config)

        # Check if httpx is available
        if httpx is None:
            raise ImportError(
                "httpx is not installed. Please install it with: pip install httpx"
            )

        # Set API base URL (required)
        self.api_base_url = getattr(self.config, 'api_base_url', None) or os.getenv(
            "RERANK_API_BASE_URL"
        )
        if not self.api_base_url:
            raise ValueError(
                "api_base_url is required. Set RERANK_API_BASE_URL environment variable "
                "or pass api_base_url in config."
            )

        # Set model (required)
        if not self.config.model:
            raise ValueError(
                "model is required. Pass model name or UID in config."
            )

        # API key is optional
        self.api_key = self.config.api_key or os.getenv("RERANK_API_KEY")

    def rerank(
        self, 
        query: str, 
        documents: List[str], 
        top_n: Optional[int] = None,
        instruct: Optional[str] = None
    ) -> List[Tuple[int, float]]:
        """
        Rerank documents based on relevance to the query using the configured rerank service.

        Args:
            query (str): The search query
            documents (List[str]): List of document texts to rerank
            top_n (Optional[int]): Number of top results to return. If None, returns all results
            instruct (Optional[str]): Instruct for rerank (not used by standard API, kept for compatibility)

        Returns:
            List[Tuple[int, float]]: List of (document_index, relevance_score) tuples,
                                     sorted by relevance score in descending order
                                     
        Raises:
            ValueError: If query is empty or documents list is empty
            Exception: If API call fails
        """
        # Validate inputs
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        
        if not documents or len(documents) == 0:
            raise ValueError("Documents list cannot be empty")

        # Clean query
        query = query.strip()
        
        # Use provided top_n or return all results
        effective_top_n = top_n if top_n is not None else len(documents)
        
        try:
            # Prepare request payload
            payload = {
                "model": self.config.model,
                "query": query,
                "documents": documents,
                "top_n": effective_top_n,
            }

            # Prepare headers
            headers = {
                "Content-Type": "application/json",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            # Make API request
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    self.api_base_url,
                    json=payload,
                    headers=headers,
                )

                # Check response status
                response.raise_for_status()
                result = response.json()

            # Parse results
            results = []
            if "results" in result:
                rerank_results = result["results"]
                # Sort by relevance_score in descending order
                sorted_results = sorted(
                    rerank_results, 
                    key=lambda x: x.get("relevance_score", 0.0), 
                    reverse=True
                )
                # Take top_n results
                for result_item in sorted_results[:effective_top_n]:
                    index = result_item.get("index")
                    score = result_item.get("relevance_score", 0.0)
                    if index is not None:
                        results.append((index, float(score)))
            else:
                raise Exception("Unexpected response format from Rerank API")

            return results

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "detail" in error_detail:
                    error_msg += f": {error_detail['detail']}"
                elif "error" in error_detail:
                    error_msg += f": {error_detail['error']}"
            except:
                error_msg += f": {e.response.text}"
            raise Exception(f"Failed to rerank documents: {error_msg}")
        except Exception as e:
            raise Exception(f"Failed to rerank documents: {e}")

    def rerank_with_texts(
        self, 
        query: str, 
        documents: List[str], 
        top_n: Optional[int] = None,
        instruct: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """
        Rerank documents and return texts with scores instead of indices.

        Args:
            query (str): The search query
            documents (List[str]): List of document texts to rerank
            top_n (Optional[int]): Number of top results to return
            instruct (Optional[str]): Instruct for rerank (not used by standard API, kept for compatibility)

        Returns:
            List[Tuple[str, float]]: List of (document_text, relevance_score) tuples,
                                     sorted by relevance score in descending order
        """
        # Get reranked indices and scores
        reranked_results = self.rerank(query, documents, top_n, instruct)
        
        # Map indices back to document texts
        results_with_texts = [
            (documents[idx], score) 
            for idx, score in reranked_results
        ]
        
        return results_with_texts

