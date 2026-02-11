import json
import logging
import os
from typing import Dict, List, Optional, Union

try:
    from dashscope import Generation
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
except ImportError:
    Generation = None
    DashScopeAPIResponse = None

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.qwen import QwenConfig
from powermem.utils.utils import extract_json
import dashscope


class QwenLLM(LLMBase):
    def __init__(self, config: Optional[Union[BaseLLMConfig, QwenConfig, Dict]] = None):
        # Check if dashscope is available first
        try:
            from dashscope import Generation
            from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
        except ImportError:
            raise ImportError(
                "DashScope SDK is not installed. Please install it with: pip install dashscope"
            )

        # Convert to QwenConfig if needed
        if config is None:
            config = QwenConfig()
        elif isinstance(config, dict):
            config = QwenConfig(**config)
        elif isinstance(config, BaseLLMConfig) and not isinstance(config, QwenConfig):
            # Convert BaseLLMConfig to QwenConfig
            config = QwenConfig(
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
            self.config.model = "qwen-turbo"

        # Set API key
        api_key = self.config.api_key or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError(
                "API key is required. Set DASHSCOPE_API_KEY environment variable or pass api_key in config.")

        # Set API key for DashScope SDK
        dashscope.api_key = api_key

        # Set base URL
        base_url = getattr(self.config, "dashscope_base_url", None) or os.getenv(
            "DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/api/v1"

        if base_url:
            os.environ["DASHSCOPE_BASE_URL"] = base_url

    def _get_attr(self, obj, key, default=None):
        """Unified handling of attribute access for both dicts and objects"""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    
    def _extract_message(self, output):
        """Extract message object from response output"""
        # Get default text content
        text = self._get_attr(output, 'text', '')
        
        # Extract message from choices
        choices = self._get_attr(output, 'choices', [])
        if choices:
            choice = choices[0]
            message = self._get_attr(choice, 'message')
            return message, text
        
        return None, text
    
    def _extract_content(self, output):
        """Extract response content"""
        message, default_text = self._extract_message(output)
        if message:
            return self._get_attr(message, 'content', default_text)
        return default_text
    
    def _extract_tool_calls(self, output):
        """Extract tool calls from response"""
        message, _ = self._extract_message(output)
        if not message:
            return []
        
        tool_calls = self._get_attr(message, 'tool_calls')
        if not tool_calls:
            return []
        
        processed_calls = []
        for tool_call in tool_calls:
            function = self._get_attr(tool_call, 'function', {})
            name = self._get_attr(function, 'name')
            arguments = self._get_attr(function, 'arguments', '{}')
            
            processed_calls.append({
                "name": name,
                "arguments": json.loads(extract_json(arguments)),
            })
        
        return processed_calls

    def _parse_response(self, response: DashScopeAPIResponse, tools: Optional[List[Dict]] = None):
        """
        Process the response based on whether tools are used or not.

        Args:
            response: The raw response from DashScope API.
            tools: The list of tools provided in the request.

        Returns:
            str or dict: The processed response.
        """
        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}: {response.message}")

        content = self._extract_content(response.output)
        
        if tools:
            return {
                "content": content,
                "tool_calls": self._extract_tool_calls(response.output),
            }
        
        return content

    def generate_response(
            self,
            messages: List[Dict[str, str]],
            response_format=None,
            tools: Optional[List[Dict]] = None,
            tool_choice: str = "auto",
            **kwargs,
    ):
        """
        Generate a response based on the given messages using Qwen.

        Args:
            messages (list): List of message dicts containing 'role' and 'content'.
            response_format (str or object, optional): Format of the response. Defaults to None.
            tools (list, optional): List of tools that the model can call. Defaults to None.
            tool_choice (str, optional): Tool choice method. Defaults to "auto".
            **kwargs: Additional Qwen-specific parameters.

        Returns:
            str or dict: The generated response.
        """
        params = self._get_supported_params(**kwargs)

        # Prepare generation parameters
        generation_params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": params.get("temperature", self.config.temperature),
            "max_tokens": params.get("max_tokens", self.config.max_tokens),
            "top_p": params.get("top_p", self.config.top_p),
        }

        # Add Qwen-specific parameters
        if getattr(self.config, "enable_search", False):
            generation_params["enable_search"] = True
            search_params = getattr(self.config, "search_params", None)
            if search_params:
                generation_params.update(search_params)

        # Add tools if provided
        if tools:
            generation_params["tools"] = tools
            generation_params["tool_choice"] = tool_choice

        # Add response format if provided
        if response_format:
            generation_params["response_format"] = response_format

        try:
            response = Generation.call(**generation_params)
            parsed_response = self._parse_response(response, tools)

            response_callback = getattr(self.config, "response_callback", None)
            if response_callback:
                try:
                    response_callback(self, response, generation_params)
                except Exception as e:
                    # Log error but don't propagate
                    logging.error(f"Error due to callback: {e}")
                    pass

            return parsed_response
        except Exception as e:
            logging.error(f"Qwen API call failed: {e}")
            raise
