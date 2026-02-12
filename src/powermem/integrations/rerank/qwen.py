"""
Qwen Rerank implementation using Alibaba Cloud DashScope API
"""
import os
from typing import List, Optional, Tuple

try:
    import dashscope
    from dashscope import TextReRank
except ImportError:
    TextReRank = None
    dashscope = None

from powermem.integrations.rerank.base import RerankBase
from powermem.integrations.rerank.config.base import BaseRerankConfig


class QwenRerank(RerankBase):
    """Qwen3 Rerank implementation using Alibaba Cloud Bailian API
    
    This implementation uses the qwen3-rerank model through DashScope SDK.
    
    Args:
        config (Optional[BaseRerankConfig]): Configuration for the rerank model
    """

    def __init__(self, config: Optional[BaseRerankConfig] = None):
        super().__init__(config)

        # Set default model (if not already set in config)
        if not self.config.model:
            self.config.model = "qwen3-rerank"

        # Check if dashscope is available
        if TextReRank is None or dashscope is None:
            raise ImportError(
                "DashScope SDK is not installed. Please install it with: pip install dashscope"
            )

        # Validate API key (config already handles env var loading)
        if not self.config.api_key:
            raise ValueError(
                "API key is required. Set DASHSCOPE_API_KEY, QWEN_API_KEY, or RERANKER_API_KEY environment variable, "
                "or pass api_key in config."
            )

        # Set API key for DashScope SDK
        dashscope.api_key = self.config.api_key
        
        # Set base URL if provided
        if self.config.api_base_url:
            dashscope.base_http_api_url = self.config.api_base_url

    def rerank(
        self, 
        query: str, 
        documents: List[str], 
        top_n: Optional[int] = None,
        instruct: Optional[str] = None
    ) -> List[Tuple[int, float]]:
        """
        Rerank documents based on relevance to the query using Qwen3 Rerank model.

        Args:
            query (str): The search query
            documents (List[str]): List of document texts to rerank
            top_n (Optional[int]): Number of top results to return. If None, uses config.top_n
            instruct (Optional[str]): Instruct for rerank

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
            # Call the Rerank API
            if instruct is not None:
                response = TextReRank.call(
                    model=self.config.model,
                    query=query,
                    documents=documents,
                    top_n=effective_top_n,
                    return_documents=False,
                    instruct=instruct
                )
            else:
                response = TextReRank.call(
                    model=self.config.model,
                    query=query,
                    documents=documents,
                    top_n=effective_top_n,
                    return_documents=False,
                )

            # Check response status
            if response.status_code != 200:
                raise Exception(
                    f"Rerank API request failed with status {response.status_code}: {response.message}"
                )

            # Parse results
            results = []
            if hasattr(response, 'output') and isinstance(response.output, dict):
                rerank_results = response.output.get('results', [])
                for result in rerank_results:
                    index = result.get('index')
                    score = result.get('relevance_score', 0.0)
                    if index is not None:
                        results.append((index, float(score)))
            else:
                raise Exception("Unexpected response format from Rerank API")

            return results

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
            instruct (Optional[str]): Instruct for rerank

        Returns:
            List[Tuple[str, float]]: List of (document_text, relevance_score) tuples,
                                     sorted by relevance score in descending order
        """
        # Get reranked indices and scores
        reranked_results = self.rerank(query, documents, top_n,instruct)
        
        # Map indices back to document texts
        results_with_texts = [
            (documents[idx], score) 
            for idx, score in reranked_results
        ]
        
        return results_with_texts

