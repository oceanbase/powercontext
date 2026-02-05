import json
import logging
import os
from typing import Dict, List, Optional, Union

from openai import OpenAI
from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.openai import OpenAIConfig
from powermem.utils.utils import extract_json


class OpenAILLM(LLMBase):
    def __init__(self, config: Optional[Union[BaseLLMConfig, OpenAIConfig, Dict]] = None):
        # Convert to OpenAIConfig if needed
        if config is None:
            config = OpenAIConfig()
        elif isinstance(config, dict):
            config = OpenAIConfig(**config)
        elif isinstance(config, BaseLLMConfig) and not isinstance(config, OpenAIConfig):
            # Convert BaseLLMConfig to OpenAIConfig
            config = OpenAIConfig(
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

        if not self.config.model:
            self.config.model = "gpt-4o-mini"

        if os.environ.get("OPENROUTER_API_KEY"):  # Use OpenRouter
            self.client = OpenAI(
                api_key=os.environ.get("OPENROUTER_API_KEY"),
                base_url=getattr(self.config, "openrouter_base_url", None)
                or os.getenv("OPENROUTER_API_BASE")
                or "https://openrouter.ai/api/v1",
            )
        else:
            api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
            base_url = getattr(self.config, "openai_base_url", None) or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"

            self.client = OpenAI(api_key=api_key, base_url=base_url)

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
        Generate a JSON response based on the given messages using OpenAI.

        Args:
            messages (list): List of message dicts containing 'role' and 'content'.
            response_format (str or object, optional): Format of the response. Defaults to "text".
            tools (list, optional): List of tools that the model can call. Defaults to None.
            tool_choice (str, optional): Tool choice method. Defaults to "auto".
            **kwargs: Additional OpenAI-specific parameters.

        Returns:
            json: The generated response.
        """
        params = self._get_supported_params(messages=messages, **kwargs)
        
        params.update({
            "model": self.config.model,
            "messages": messages,
        })

        if os.getenv("OPENROUTER_API_KEY"):
            openrouter_params = {}
            models = getattr(self.config, "models", None)
            if models:
                openrouter_params["models"] = models
                openrouter_params["route"] = getattr(self.config, "route", "fallback")
                params.pop("model")

            site_url = getattr(self.config, "site_url", None)
            app_name = getattr(self.config, "app_name", None)
            if site_url and app_name:
                extra_headers = {
                    "HTTP-Referer": site_url,
                    "X-Title": app_name,
                }
                openrouter_params["extra_headers"] = extra_headers

            params.update(**openrouter_params)
        
        else:
            openai_specific_generation_params = ["store"]
            for param in openai_specific_generation_params:
                if hasattr(self.config, param):
                    params[param] = getattr(self.config, param)
            
        if response_format:
            params["response_format"] = response_format
        if tools:  # TODO: Remove tools if no issues found with new memory addition logic
            params["tools"] = tools
            params["tool_choice"] = tool_choice
        response = self.client.chat.completions.create(**params)
        parsed_response = self._parse_response(response, tools)
        response_callback = getattr(self.config, "response_callback", None)
        if response_callback:
            try:
                response_callback(self, response, params)
            except Exception as e:
                # Log error but don't propagate
                logging.error(f"Error due to callback: {e}")
                pass
        return parsed_response
