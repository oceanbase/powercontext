from typing import Literal, Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureOpenAIEmbedding(EmbeddingBase):
    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        api_key = getattr(self.config, "api_key", None)
        azure_deployment = getattr(self.config, "azure_deployment", None)
        azure_endpoint = getattr(self.config, "azure_endpoint", None)
        api_version = getattr(self.config, "api_version", None)
        default_headers = getattr(self.config, "default_headers", None)

        # If the API key is not provided or is a placeholder, use DefaultAzureCredential.
        if api_key is None or api_key == "" or api_key == "your-api-key":
            self.credential = DefaultAzureCredential()
            azure_ad_token_provider = get_bearer_token_provider(
                self.credential,
                SCOPE,
            )
            api_key = None
        else:
            azure_ad_token_provider = None

        self.client = AzureOpenAI(
            azure_deployment=azure_deployment,
            azure_endpoint=azure_endpoint,
            azure_ad_token_provider=azure_ad_token_provider,
            api_version=api_version,
            api_key=api_key,
            http_client=getattr(self.config, "http_client", None),
            default_headers=default_headers,
        )

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using OpenAI.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        text = text.replace("\n", " ")
        return self.client.embeddings.create(input=[text], model=self.config.model).data[0].embedding
