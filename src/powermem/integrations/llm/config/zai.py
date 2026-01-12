from typing import Any, Callable, List, Optional

from powermem.integrations.llm.config.base import BaseLLMConfig


class ZaiConfig(BaseLLMConfig):
    """
    Configuration class for Zhipu AI (Z.ai) specific parameters.
    Inherits from BaseLLMConfig and adds Zhipu AI-specific settings.
    
    Reference: https://docs.bigmodel.cn/cn/guide/develop/python/introduction
    """

    def __init__(
            self,
            # Base parameters
            model: Optional[str] = None,
            temperature: float = 0.1,
            api_key: Optional[str] = None,
            max_tokens: int = 2000,
            top_p: float = 0.1,
            top_k: int = 1,
            enable_vision: bool = False,
            vision_details: Optional[str] = "auto",
            http_client_proxies: Optional[dict] = None,
            # Zhipu AI-specific parameters
            zai_base_url: Optional[str] = None,
            # Response monitoring callback
            response_callback: Optional[Callable[[Any, dict, dict], None]] = None,
    ):
        """
        Initialize Zhipu AI configuration.

        Args:
            model: Zhipu AI model to use (e.g., 'glm-4.7', 'glm-4.6v'), defaults to None
            temperature: Controls randomness, defaults to 0.1
            api_key: Zhipu AI API key, defaults to None
            max_tokens: Maximum tokens to generate, defaults to 2000
            top_p: Nucleus sampling parameter, defaults to 0.1
            top_k: Top-k sampling parameter, defaults to 1
            enable_vision: Enable vision capabilities (use glm-4.6v model), defaults to False
            vision_details: Vision detail level, defaults to "auto"
            http_client_proxies: HTTP client proxy settings, defaults to None
            zai_base_url: Zhipu AI API base URL, defaults to None
            response_callback: Optional callback for monitoring LLM responses.
        """
        # Initialize base parameters
        super().__init__(
            model=model,
            temperature=temperature,
            api_key=api_key,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            enable_vision=enable_vision,
            vision_details=vision_details,
            http_client_proxies=http_client_proxies,
        )

        # Zhipu AI-specific parameters
        self.zai_base_url = zai_base_url or "https://open.bigmodel.cn/api/paas/v4/"

        # Response monitoring
        self.response_callback = response_callback
