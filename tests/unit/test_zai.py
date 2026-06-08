from unittest.mock import Mock, patch

import pytest

from powermem.integrations.llm.config.zai import ZaiConfig
from powermem.integrations.llm.zai import ZaiLLM


@pytest.fixture
def mock_openai_client():
    with patch("powermem.integrations.llm.zai.OpenAI") as mock_openai:
        mock_client = Mock()
        mock_openai.return_value = mock_client
        yield mock_client


def test_zai_llm_base_url(monkeypatch):
    monkeypatch.delenv("ZAI_BASE_URL", raising=False)

    # case 1: default base_url from ZaiConfig
    default_url = "https://open.bigmodel.cn/api/paas/v4/"
    llm = ZaiLLM(ZaiConfig(api_key="fake"))
    assert str(llm.client.base_url) == default_url

    # case 2: ZAI_BASE_URL env var (read by ZaiConfig via AliasChoices)
    env_url = "https://via-env.example.com/v1"
    monkeypatch.setenv("ZAI_BASE_URL", env_url)
    llm = ZaiLLM(ZaiConfig(api_key="fake"))
    assert str(llm.client.base_url) == env_url + "/"
    monkeypatch.delenv("ZAI_BASE_URL")

    # case 3: config field zai_base_url
    config_url = "https://via-config.example.com/v1"
    llm = ZaiLLM(ZaiConfig(api_key="fake", zai_base_url=config_url))
    assert str(llm.client.base_url) == config_url + "/"


def test_generate_response_without_tools(mock_openai_client):
    config = ZaiConfig(model="glm-4.7", temperature=0.7, max_tokens=100, top_p=1.0, api_key="fake")
    llm = ZaiLLM(config)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]

    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content="Hi there!", tool_calls=None))]
    mock_openai_client.chat.completions.create.return_value = mock_response

    response = llm.generate_response(messages)

    mock_openai_client.chat.completions.create.assert_called_once()
    assert response == "Hi there!"


def test_generate_response_with_tools(mock_openai_client):
    config = ZaiConfig(model="glm-4.7", api_key="fake")
    llm = ZaiLLM(config)
    messages = [{"role": "user", "content": "Add a memory."}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_memory",
                "description": "Add a memory",
                "parameters": {
                    "type": "object",
                    "properties": {"data": {"type": "string"}},
                    "required": ["data"],
                },
            },
        }
    ]

    mock_tool_call = Mock()
    mock_tool_call.function.name = "add_memory"
    mock_tool_call.function.arguments = '{"data": "test memory"}'

    mock_message = Mock()
    mock_message.content = "Done."
    mock_message.tool_calls = [mock_tool_call]
    mock_response = Mock()
    mock_response.choices = [Mock(message=mock_message)]
    mock_openai_client.chat.completions.create.return_value = mock_response

    response = llm.generate_response(messages, tools=tools)

    assert response["content"] == "Done."
    assert len(response["tool_calls"]) == 1
    assert response["tool_calls"][0]["name"] == "add_memory"
    assert response["tool_calls"][0]["arguments"] == {"data": "test memory"}


def test_response_callback_invocation(mock_openai_client):
    mock_callback = Mock()
    config = ZaiConfig(model="glm-4.7", api_key="fake", response_callback=mock_callback)
    llm = ZaiLLM(config)
    messages = [{"role": "user", "content": "Test"}]

    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content="OK", tool_calls=None))]
    mock_openai_client.chat.completions.create.return_value = mock_response

    llm.generate_response(messages)

    mock_callback.assert_called_once()
    args = mock_callback.call_args[0]
    assert args[0] is llm
    assert args[1] == mock_response
    assert "messages" in args[2]


def test_callback_exception_does_not_propagate(mock_openai_client):
    def faulty_callback(*args):
        raise ValueError("boom")

    config = ZaiConfig(model="glm-4.7", api_key="fake", response_callback=faulty_callback)
    llm = ZaiLLM(config)
    messages = [{"role": "user", "content": "Test"}]

    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content="OK", tool_calls=None))]
    mock_openai_client.chat.completions.create.return_value = mock_response

    response = llm.generate_response(messages)
    assert response == "OK"
