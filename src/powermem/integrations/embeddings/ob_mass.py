import os
import uuid
from typing import List, Literal, Optional

import httpx

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class OBMassEmbedding(EmbeddingBase):
    """
    OceanBase MASS (OBAI) embedding provider.

    Sends requests to the OBAI gateway ``/embeddings`` endpoint with the
    non-standard body format expected by the service::

        {
          "model": "<model>",
          "input": {"contents": [{"text": "<text>"}]},
          "dimensions": <dims>
        }

    Headers:
    - ``Authorization: Bearer <api_key>``
    - ``X-Request-ID: <request_id>``  (auto-generated if not configured)
    - ``X-OB-Project-ID: <project_id>``  (optional)
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        self.config.model = self.config.model or "qwen3-vl-embedding"
        self.config.embedding_dims = self.config.embedding_dims or 1536

        self.api_key = (
            self.config.api_key
            or os.getenv("OB_MASS_API_KEY")
        )
        if not self.api_key:
            raise ValueError(
                "API key is required for ob_mass provider. "
                "Set config api_key or OB_MASS_API_KEY env var."
            )

        self.base_url = (
            getattr(self.config, "openai_base_url", None)
            or os.getenv("OB_MASS_BASE_URL")
        )
        if not self.base_url:
            raise ValueError(
                "Base URL is required for ob_mass provider. "
                "Set config openai_base_url or OB_MASS_BASE_URL env var."
            )
        # Strip trailing slash for consistent URL construction
        self.base_url = self.base_url.rstrip("/")

        self.project_id = (
            getattr(self.config, "project_id", None)
            or os.getenv("OB_MASS_PROJECT_ID")
        )

        self.request_id = getattr(self.config, "request_id", None)

        self._client = httpx.Client(timeout=60.0)

    def embed(
        self,
        text: str,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ):
        text = text.replace("\n", " ")

        request_id = self.request_id or str(uuid.uuid4())

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }
        if self.project_id:
            headers["X-OB-Project-ID"] = str(self.project_id)

        payload = {
            "model": self.config.model,
            "input": {"contents": [{"text": text}]},
            "dimensions": self.config.embedding_dims,
        }

        url = f"{self.base_url}/embeddings"
        response = self._client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            # Parse error body if possible, but never expose the api_key
            try:
                error_body = response.json()
                error_msg = error_body.get("error", {}).get("message", response.text)
            except Exception:
                error_msg = response.text
            raise RuntimeError(
                f"ob_mass embedding request failed: "
                f"status={response.status_code}, message={error_msg}"
            )

        data = response.json()

        # Compatible with OpenAI-like response: data[0].embedding
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"ob_mass embedding response has unexpected format: {exc}"
            ) from exc

    def embed_batch(
        self,
        texts: List[str],
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> List[List[float]]:
        """Batch embed multiple texts in a single OBAI request.

        The OBAI ``/embeddings`` endpoint accepts multiple items in
        ``input.contents``, so one HTTP call is enough for the whole batch.
        """
        cleaned = [t.replace("\n", " ") for t in texts]

        request_id = self.request_id or str(uuid.uuid4())

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }
        if self.project_id:
            headers["X-OB-Project-ID"] = str(self.project_id)

        payload = {
            "model": self.config.model,
            "input": {"contents": [{"text": t} for t in cleaned]},
            "dimensions": self.config.embedding_dims,
        }

        url = f"{self.base_url}/embeddings"
        response = self._client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            try:
                error_body = response.json()
                error_msg = error_body.get("error", {}).get("message", response.text)
            except Exception:
                error_msg = response.text
            raise RuntimeError(
                f"ob_mass batch embedding request failed: "
                f"status={response.status_code}, message={error_msg}"
            )

        data = response.json()

        try:
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            return [item["embedding"] for item in items]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"ob_mass batch embedding response has unexpected format: {exc}"
            ) from exc
