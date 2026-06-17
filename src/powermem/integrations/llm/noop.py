from typing import Dict, List, Optional, Union

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.noop import NoopConfig


class NoopLLM(LLMBase):
    """LLM implementation used when language-model features are disabled."""

    is_noop = True

    def __init__(self, config: Optional[Union[BaseLLMConfig, NoopConfig, Dict]] = None):
        if config is None:
            config = NoopConfig()
        elif isinstance(config, dict):
            config = NoopConfig(**config)
        elif isinstance(config, BaseLLMConfig) and not isinstance(config, NoopConfig):
            config = NoopConfig(
                model=config.model or "noop",
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                top_p=config.top_p,
                top_k=config.top_k,
            )

        super().__init__(config)

        if not self.config.model:
            self.config.model = "noop"

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        **kwargs,
    ):
        if tools:
            return {"content": "", "tool_calls": []}
        return ""
