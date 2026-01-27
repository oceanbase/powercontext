import os
from typing import Literal, Optional

from openai import OpenAI

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class SiliconFlowEmbedding(EmbeddingBase):
    """
    SiliconFlow embedding provider implementation.

    SiliconFlow is compatible with OpenAI API format, so we can reuse the official
    openai SDK by pointing base_url to SiliconFlow's endpoint.
    Default base URL: https://api.siliconflow.cn/v1
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        api_key = (
            self.config.api_key
            or os.getenv("EMBEDDING_API_KEY")
        )
        base_url = (
            getattr(self.config, "siliconflow_base_url", None)
            or os.getenv("SILICONFLOW_EMBEDDING_BASE_URL")
            or "https://api.siliconflow.cn/v1"
        )

        # Do not force a default model name here because SiliconFlow may expose
        # different embedding model identifiers; require caller/env to provide it.
        if not self.config.model:
            raise ValueError(
                "Embedding model is required for SiliconFlow. "
                "Set `config['embedder']['config']['model']` or EMBEDDING_MODEL."
            )

        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def embed(
        self,
        text: str,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ):
        text = text.replace("\n", " ")

        # Only pass `dimensions` when explicitly provided; some OpenAI-compatible
        # servers don't support it and may error.
        if self.config.embedding_dims:
            return (
                self.client.embeddings.create(
                    input=[text],
                    model=self.config.model,
                    dimensions=self.config.embedding_dims,
                )
                .data[0]
                .embedding
            )

        return self.client.embeddings.create(input=[text], model=self.config.model).data[0].embedding

