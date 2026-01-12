import json
import logging
import os
from typing import Dict, List, Optional, Union

from zai import ZhipuAiClient

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.zai import ZaiConfig
from powermem.utils.utils import extract_json


class ZaiLLM(LLMBase):
    """
    Zhipu AI (Z.ai) LLM implementation.
    
    Reference: https://docs.bigmodel.cn/cn/guide/develop/python/introduction
    
    Supported models:
    - glm-4.7: Latest language model
    - glm-4.6v: Vision understanding model
    - glm-4: General language model
    """

    def __init__(self, config: Optional[Union[BaseLLMConfig, ZaiConfig, Dict]] = None):
        # Convert to ZaiConfig if needed
        if config is None:
            config = ZaiConfig()
        elif isinstance(config, dict):
            config = ZaiConfig(**config)
        elif isinstance(config, BaseLLMConfig) and not isinstance(config, ZaiConfig):
            # Convert BaseLLMConfig to ZaiConfig
            config = ZaiConfig(
                model=config.model,
                temperature=config.temperature,
                api_key=config.api_key,
                max_tokens=config.max_tokens,
                top_p=config.top_p,
                top_k=config.top_k,
                enable_vision=config.enable_vision,
                vision_details=config.vision_details,
                http_client_proxies=config.http_client,
            )

        super().__init__(config)

        # Set default model if not specified
        if not self.config.model:
            self.config.model = "glm-4.7"

        # Get API key from config or environment
        api_key = self.config.api_key or os.getenv("ZAI_API_KEY")

        # Initialize Zhipu AI client
        self.client = ZhipuAiClient(api_key=api_key)

    def _parse_response(self, response, tools):
        """
        Process the response based on whether tools are used or not.

        Args:
            response: The raw response from API.
            tools: The list of tools provided in the request.

        Returns:
            str or dict: The processed response.
        """
        if tools:
            processed_response = {
                "content": response.choices[0].message.content,
                "tool_calls": [],
            }

            if response.choices[0].message.tool_calls:
                for tool_call in response.choices[0].message.tool_calls:
                    # Extract and validate arguments
                    arguments_str = extract_json(tool_call.function.arguments)

                    # Check if arguments are empty or whitespace only
                    if not arguments_str or arguments_str.strip() == "":
                        logging.warning(
                            f"Tool call '{tool_call.function.name}' has empty arguments. Skipping this tool call."
                        )
                        continue

                    # Try to parse JSON with error handling
                    try:
                        arguments = json.loads(arguments_str)
                    except json.JSONDecodeError as e:
                        logging.error(
                            f"Failed to parse tool call arguments for '{tool_call.function.name}': "
                            f"{arguments_str[:100]}... Error: {e}"
                        )
                        continue

                    processed_response["tool_calls"].append(
                        {
                            "name": tool_call.function.name,
                            "arguments": arguments,
                        }
                    )

            return processed_response
        else:
            return response.choices[0].message.content

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format=None,
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        **kwargs,
    ):
        """
        Generate a response based on the given messages using Zhipu AI.

        Args:
            messages (list): List of message dicts containing 'role' and 'content'.
            response_format (str or object, optional): Format of the response. Defaults to None.
            tools (list, optional): List of tools that the model can call. Defaults to None.
            tool_choice (str, optional): Tool choice method. Defaults to "auto".
            **kwargs: Additional Zhipu AI-specific parameters.

        Returns:
            str or dict: The generated response.
        """
        params = self._get_supported_params(messages=messages, **kwargs)

        params.update({
            "model": self.config.model,
            "messages": messages,
        })

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**params)
        parsed_response = self._parse_response(response, tools)

        if self.config.response_callback:
            try:
                self.config.response_callback(self, response, params)
            except Exception as e:
                # Log error but don't propagate
                logging.error(f"Error due to callback: {e}")
                pass

        return parsed_response
