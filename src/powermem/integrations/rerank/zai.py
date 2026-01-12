"""
Zhipu AI (Z.ai) Rerank implementation

Reference: https://docs.bigmodel.cn/api-reference/模型-api/文本重排序
"""
import os
from typing import List, Optional, Tuple

try:
    import httpx
except ImportError:
    httpx = None

from powermem.integrations.rerank.base import RerankBase
from powermem.integrations.rerank.config.base import BaseRerankConfig


class ZaiRerank(RerankBase):
    """Zhipu AI Rerank implementation
    
    This implementation uses the Zhipu AI rerank model through HTTP API.
    
    Args:
        config (Optional[BaseRerankConfig]): Configuration for the rerank model
    """

    def __init__(self, config: Optional[BaseRerankConfig] = None):
        super().__init__(config)

        # Set default model
        self.config.model = self.config.model or "rerank"

        # Check if httpx is available
        if httpx is None:
            raise ImportError(
                "httpx is not installed. Please install it with: pip install httpx"
            )

        # Set API key
        api_key = self.config.api_key or os.getenv("ZAI_API_KEY")
        if not api_key:
            raise ValueError(
                "API key is required. Set ZAI_API_KEY environment variable or pass api_key in config."
            )

        self.api_key = api_key
        self.api_base_url = getattr(self.config, 'api_base_url', None) or os.getenv(
            "ZAI_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/rerank"
        )

    def rerank(
        self, 
        query: str, 
        documents: List[str], 
        top_n: Optional[int] = None,
        return_documents: bool = False,
        return_raw_scores: bool = False
    ) -> List[Tuple[int, float]]:
        """
        Rerank documents based on relevance to the query using Zhipu AI Rerank model.

        Args:
            query (str): The search query (max 4096 characters)
            documents (List[str]): List of document texts to rerank (max 128 documents, each max 4096 characters)
            top_n (Optional[int]): Number of top results to return. If None or 0, returns all
            return_documents (bool): Whether to return original documents in response
            return_raw_scores (bool): Whether to return raw scores

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

        if len(documents) > 128:
            raise ValueError("Documents list cannot exceed 128 items")

        # Clean query
        query = query.strip()
        
        # Build request payload
        payload = {
            "model": self.config.model,
            "query": query,
            "documents": documents,
            "return_documents": return_documents,
            "return_raw_scores": return_raw_scores
        }

        # Add top_n if specified and greater than 0
        if top_n is not None and top_n > 0:
            payload["top_n"] = top_n

        try:
            # Make API request
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    self.api_base_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                )

                # Check response status
                response.raise_for_status()
                result = response.json()

            # Parse results
            results = []
            if "results" in result:
                rerank_results = result["results"]
                for result_item in rerank_results:
                    index = result_item.get("index")
                    score = result_item.get("relevance_score", 0.0)
                    if index is not None:
                        results.append((index, float(score)))
            else:
                raise Exception("Unexpected response format from Zhipu AI Rerank API")

            return results

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code}"
            try:
                error_detail = e.response.json()
                if "detail" in error_detail:
                    error_msg += f": {error_detail['detail']}"
            except:
                error_msg += f": {e.response.text}"
            raise Exception(f"Failed to rerank documents: {error_msg}")
        except Exception as e:
            raise Exception(f"Failed to rerank documents: {e}")

    def rerank_with_texts(
        self, 
        query: str, 
        documents: List[str], 
        top_n: Optional[int] = None
    ) -> List[Tuple[str, float]]:
        """
        Rerank documents and return texts with scores instead of indices.

        Args:
            query (str): The search query
            documents (List[str]): List of document texts to rerank
            top_n (Optional[int]): Number of top results to return

        Returns:
            List[Tuple[str, float]]: List of (document_text, relevance_score) tuples,
                                     sorted by relevance score in descending order
        """
        # Get reranked indices and scores
        reranked_results = self.rerank(query, documents, top_n)
        
        # Map indices back to document texts
        results_with_texts = [
            (documents[idx], score) 
            for idx, score in reranked_results
        ]
        
        return results_with_texts
