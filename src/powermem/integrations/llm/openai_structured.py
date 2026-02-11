import json
import os
from typing import Dict, List, Optional, Any

from openai import OpenAI

from powermem.integrations.llm import LLMBase
from powermem.integrations.llm.config.base import BaseLLMConfig


class OpenAIStructuredLLM(LLMBase):
    def __init__(self, config: Optional[BaseLLMConfig] = None):
        super().__init__(config)

        if not self.config.model:
            self.config.model = "gpt-5"

        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        base_url = getattr(self.config, "openai_base_url", None) or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1"
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate_response(
            self,
            messages: List[Dict[str, str]],
            response_format: Optional[str] = None,
            tools: Optional[List[Dict]] = None,
            tool_choice: str = "auto",
    ) -> str | None | dict[str, str | None | list[Any]]:
        """
        Generate a response based on the given messages using OpenAI.

        Args:
            messages (List[Dict[str, str]]): A list of dictionaries, each containing a 'role' and 'content' key.
            response_format (Optional[str]): The desired format of the response. Defaults to None.


        Returns:
            str: The generated response.
        """
        params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        response = self.client.beta.chat.completions.parse(**params)

        message = response.choices[0].message

        # If tools were used and tool_calls exist, return structured response
        if tools and message.tool_calls:
            tool_calls_list = []
            for tool_call in message.tool_calls:
                arguments = tool_call.function.arguments
                # Parse arguments if it's a string
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                tool_calls_list.append({
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "arguments": arguments
                })

            return {
                "content": message.content,
                "tool_calls": tool_calls_list
            }

        # Otherwise return just the content
        return message.content or ""
