import json
import logging
import os
from typing import Dict, List, Optional, Union

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.azure import AzureOpenAIConfig
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.utils.utils import extract_json

try:
    from openai import AzureOpenAI
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
except ImportError:
    raise ImportError(
        "The 'openai' and 'azure-identity' libraries are required. "
        "Please install them using 'pip install openai azure-identity'."
    )


class AzureLLM(LLMBase):
    def __init__(self, config: Optional[Union[BaseLLMConfig, AzureOpenAIConfig, Dict]] = None):
        # Convert to AzureOpenAIConfig if needed
        if config is None:
            config = AzureOpenAIConfig()
        elif isinstance(config, dict):
            config = AzureOpenAIConfig(**config)
        elif isinstance(config, BaseLLMConfig) and not isinstance(config, AzureOpenAIConfig):
            # Convert BaseLLMConfig to AzureOpenAIConfig
            config = AzureOpenAIConfig(
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
            self.config.model = "gpt-4o"

        # Get Azure endpoint from config or environment
        azure_endpoint = (
            self.config.azure_endpoint
            or os.getenv("AZURE_OPENAI_ENDPOINT")
            or os.getenv("ENDPOINT_URL")
        )

        if not azure_endpoint:
            raise ValueError(
                "Azure endpoint is required. "
                "Please provide azure_endpoint in config or set AZURE_OPENAI_ENDPOINT/ENDPOINT_URL environment variable."
            )

        # Get API version from config or environment
        api_version = (
            self.config.api_version
            or os.getenv("AZURE_OPENAI_API_VERSION")
            or "2025-01-01-preview"
        )

        # Initialize Azure OpenAI client
        # Support both API key and Azure AD token authentication
        if self.config.azure_ad_token_provider:
            # Use Azure AD token provider (Entra ID authentication)
            self.client = AzureOpenAI(
                azure_endpoint=azure_endpoint,
                azure_ad_token_provider=self.config.azure_ad_token_provider,
                api_version=api_version,
            )
        else:
            # Use API key authentication
            api_key = self.config.api_key or os.getenv("AZURE_OPENAI_API_KEY")
            if not api_key:
                # Try to use DefaultAzureCredential if no API key is provided
                try:
                    token_provider = get_bearer_token_provider(
                        DefaultAzureCredential(),
                        "https://cognitiveservices.azure.com/.default"
                    )
                    self.client = AzureOpenAI(
                        azure_endpoint=azure_endpoint,
                        azure_ad_token_provider=token_provider,
                        api_version=api_version,
                    )
                except Exception as e:
                    raise ValueError(
                        f"Either api_key or azure_ad_token_provider must be provided. "
                        f"You can also set AZURE_OPENAI_API_KEY environment variable. "
                        f"Attempted to use DefaultAzureCredential but failed: {e}"
                    )
            else:
                self.client = AzureOpenAI(
                    azure_endpoint=azure_endpoint,
                    api_key=api_key,
                    api_version=api_version,
                )

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
        Generate a response based on the given messages using Azure OpenAI.

        Args:
            messages (list): List of message dicts containing 'role' and 'content'.
            response_format (str or object, optional): Format of the response. Defaults to "text".
            tools (list, optional): List of tools that the model can call. Defaults to None.
            tool_choice (str, optional): Tool choice method. Defaults to "auto".
            **kwargs: Additional Azure OpenAI-specific parameters.

        Returns:
            json: The generated response.
        """
        params = self._get_supported_params(messages=messages, **kwargs)

        params.update(
            {
                "model": self.config.model,
                "messages": messages,
            }
        )

        if response_format:
            params["response_format"] = response_format
        if tools:  # TODO: Remove tools if no issues found with new memory addition logic
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**params)
        parsed_response = self._parse_response(response, tools)

        if hasattr(self.config, "response_callback") and self.config.response_callback:
            try:
                self.config.response_callback(self, response, params)
            except Exception as e:
                # Log error but don't propagate
                logging.error(f"Error due to callback: {e}")
                pass

        return parsed_response
