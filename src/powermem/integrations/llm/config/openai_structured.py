from powermem.integrations.llm.config.openai import OpenAIConfig
from powermem.settings import settings_config


class OpenAIStructuredConfig(OpenAIConfig):
    """
    Configuration class for OpenAI Structured Output.
    Inherits all configuration from OpenAIConfig, only overrides metadata.
    """

    _provider_name = "openai_structured"
    _class_path = "powermem.integrations.llm.openai_structured.OpenAIStructuredLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)
