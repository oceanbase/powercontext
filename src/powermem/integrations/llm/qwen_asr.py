import logging
import os
from typing import Dict, List, Optional, Union

try:
    import dashscope
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
except ImportError:
    dashscope = None
    DashScopeAPIResponse = None

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.qwen_asr import QwenASRConfig


class QwenASR(LLMBase):
    """
    Qwen ASR (Automatic Speech Recognition) integration.
    Converts audio to text using Qwen ASR models.
    """

    def __init__(self, config: Optional[Union[BaseLLMConfig, QwenASRConfig, Dict]] = None):
        # Check if dashscope is available first
        if dashscope is None:
            raise ImportError(
                "DashScope SDK is not installed. Please install it with: pip install dashscope"
            )

        # Convert to QwenASRConfig if needed
        if config is None:
            config = QwenASRConfig()
        elif isinstance(config, dict):
            config = QwenASRConfig(**config)
        elif isinstance(config, BaseLLMConfig) and not isinstance(config, QwenASRConfig):
            # Convert BaseLLMConfig to QwenASRConfig (only use model and api_key for ASR)
            config = QwenASRConfig(
                model=config.model,
                api_key=config.api_key,
            )

        super().__init__(config)

        if not self.config.model:
            self.config.model = "qwen3-asr-flash"

        # Set API key
        api_key = self.config.api_key or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError(
                "API key is required. Set DASHSCOPE_API_KEY environment variable or pass api_key in config."
            )

        # Set API key for DashScope SDK
        dashscope.api_key = api_key

        # Set base URL
        base_url = getattr(self.config, "dashscope_base_url", None) or os.getenv(
            "DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/api/v1"

        if base_url:
            dashscope.base_http_api_url = base_url

    def _parse_response(self, response: DashScopeAPIResponse) -> str:
        """
        Parse the ASR response and extract text content.

        Args:
            response: The raw response from DashScope API.

        Returns:
            str: The extracted text content.
        """
        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}: {response.message}")

        try:
            choices = response.output.choices
            if choices:
                content = choices[0].message.content
                if content:
                    # Extract all text fields from content list
                    texts = [item["text"] for item in content if "text" in item]
                    # Join all text segments
                    return " ".join(texts)
            return ""
        except Exception as e:
            logging.error(f"Error parsing ASR response: {e}")
            raise

    def generate_response(
            self,
            messages: List[Dict[str, Union[str, List]]],
            **kwargs,
    ):
        """
        Generate text response from audio input using Qwen ASR.

        Args:
            messages: List of message dicts. For ASR, messages should contain audio content.
                Example:
                [
                    {
                        "role": "system",
                        "content": [{"text": ""}]
                    },
                    {
                        "role": "user",
                        "content": [{"audio": "https://example.com/audio.wav"}]
                    }
                ]
            **kwargs: Additional ASR-specific parameters.

        Returns:
            str: The transcribed text from the audio.
        """
        # Prepare ASR parameters
        asr_params = {
            "api_key": self.config.api_key or os.getenv("DASHSCOPE_API_KEY"),
            "model": self.config.model,
            "messages": messages,
            "result_format": getattr(self.config, "result_format", "message"),
        }

        # Add ASR options
        config_asr_options = getattr(self.config, "asr_options", None)
        asr_options = kwargs.get("asr_options", config_asr_options)
        if asr_options:
            asr_params["asr_options"] = asr_options

        # Add any other kwargs
        for key, value in kwargs.items():
            if key not in ["asr_options"]:
                asr_params[key] = value

        try:
            response = dashscope.MultiModalConversation.call(**asr_params)
            return self._parse_response(response)
        except Exception as e:
            logging.error(f"Qwen ASR API call failed: {e}")
            raise

    def transcribe(
            self,
            audio_url: str,
            system_text: Optional[str] = "",
            asr_options: Optional[dict] = None,
    ) -> str:
        """
        Convenience method to transcribe audio from URL file path.

        Args:
            audio_url: URL file path to the audio file.
            system_text: Optional system message text for customization context.
            asr_options: Optional ASR-specific options (e.g., {"language": "zh", "enable_itn": True}).

        Returns:
            str: The transcribed text from the audio.
        """
        messages = [
            {
                "role": "system",
                "content": [{"text": system_text}]
            },
            {
                "role": "user",
                "content": [{"audio": audio_url}]
            }
        ]

        return self.generate_response(messages, asr_options=asr_options or getattr(self.config, "asr_options", None))

