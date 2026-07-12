"""
NVIDIA NIM Rerank implementation

Targets the NVIDIA NIM / NeMo Retriever Reranking `/v1/ranking` endpoint
(e.g. baai/bge-reranker-v2-m3). Unlike the unified/Cohere-style rerank API,
NIM wraps the query and each passage in objects and returns a flat
`rankings` array scored by the cross-encoder.

Request:
    POST {api_base_url}/ranking
    {
        "model": "baai/bge-reranker-v2-m3",
        "query": {"text": "..."},
        "passages": [{"text": "..."}, ...]
    }

Response:
    {
        "rankings": [
            {"index": 0, "logit": -9.09},
            {"index": 1, "logit": -11.02}
        ],
        "usage": {"prompt_tokens": 31, "total_tokens": 31}
    }

Note: do NOT send `return_text` — the NIM /v1/ranking endpoint rejects unknown
fields with a validation error. Scores come back as `logit` (no `score` field);
the parser falls back to `logit` when `score` is absent.
"""
from typing import List, Optional, Tuple

try:
    import httpx
except ImportError:
    httpx = None

from powermem.integrations.rerank.base import RerankBase
from powermem.integrations.rerank.config.base import BaseRerankConfig


def _ranking_url(api_base_url: str) -> str:
    """Normalize the configured base URL to the NIM `/v1/ranking` endpoint.

    Accepts any of: `http://h:8002`, `http://h:8002/v1`,
    `http://h:8002/v1/ranking` (idempotent).
    """
    url = (api_base_url or "").rstrip("/")
    if url.endswith("/ranking"):
        return url
    if url.endswith("/v1"):
        return url + "/ranking"
    return url + "/v1/ranking"


class NimRerank(RerankBase):
    """Rerank via the NVIDIA NIM `/v1/ranking` endpoint.

    Args:
        config (Optional[BaseRerankConfig]):
            - api_base_url: NIM rerank base URL, e.g. http://host:8002/v1 (required)
            - model: NIM model name, e.g. baai/bge-reranker-v2-m3 (required)
            - api_key: Optional NGC / NIM API key (sent as Bearer)
    """

    def __init__(self, config: Optional[BaseRerankConfig] = None):
        super().__init__(config)

        if httpx is None:
            raise ImportError(
                "httpx is not installed. Please install it with: pip install httpx"
            )

        if not self.config.api_base_url:
            raise ValueError(
                "api_base_url is required. Set RERANKER_API_BASE_URL / NIM_RERANK_BASE_URL "
                "or pass api_base_url in config."
            )
        if not self.config.model:
            raise ValueError(
                "model is required. Set RERANK_MODEL or pass model name in config."
            )

        self.api_base_url = self.config.api_base_url
        self.api_key = self.config.api_key
        self.http_client = self.config.http_client

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_n: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> List[Tuple[int, float]]:
        """Rerank documents via the NIM `/v1/ranking` endpoint.

        Returns: list of (document_index, score) sorted by score desc, sliced to top_n.
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        if not documents:
            raise ValueError("Documents list cannot be empty")

        query = query.strip()
        effective_top_n = top_n if top_n is not None else len(documents)

        payload = {
            "model": self.config.model,
            "query": {"text": query},
            "passages": [{"text": d} for d in documents],
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = _ranking_url(self.api_base_url)

        try:
            if self.http_client:
                response = self.http_client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
            else:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    result = response.json()
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code}"
            try:
                detail = e.response.json()
                error_msg += f": {detail.get('detail') or detail.get('error') or detail}"
            except (ValueError, KeyError):
                error_msg += f": {e.response.text}"
            raise Exception(f"NIM rerank failed: {error_msg}")
        except Exception as e:
            raise Exception(f"NIM rerank failed: {e}")

        rankings = result.get("rankings")
        if not rankings:
            raise Exception(f"Unexpected response format from NIM Rerank API: {result}")

        scored = []
        for item in rankings:
            idx = item.get("index")
            if idx is None:
                continue
            score = item.get("score")
            if score is None:
                score = item.get("logit", 0.0)
            scored.append((int(idx), float(score)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:effective_top_n]

    def rerank_with_texts(
        self,
        query: str,
        documents: List[str],
        top_n: Optional[int] = None,
        instruct: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Rerank and return (document_text, score) sorted by score desc."""
        return [
            (documents[idx], score)
            for idx, score in self.rerank(query, documents, top_n, instruct)
        ]
