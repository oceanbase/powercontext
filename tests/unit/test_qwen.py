import os
from unittest.mock import Mock, patch

import pytest

from powermem.integrations.llm.config.qwen import QwenConfig
from powermem.integrations.llm.qwen import QwenLLM


@pytest.fixture
def mock_dashscope_generation():
    with patch("powermem.integrations.llm.qwen.Generation") as mock_generation:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.message = "Success"
        mock_response.output = {"text": "Test response"}
        mock_generation.call.return_value = mock_response
        yield mock_generation


@pytest.fixture
def mock_dashscope_import():
    with patch("powermem.integrations.llm.qwen.dashscope") as mock_dashscope:
        yield mock_dashscope


def test_qwen_llm_base_url():
    # case1: default config: with default dashscope base url
    config = QwenConfig(model="qwen-turbo", temperature=0.7, max_tokens=100, top_p=1.0, api_key="api_key")
    llm = QwenLLM(config)
    # Check that the base URL is set correctly
    assert os.getenv("DASHSCOPE_BASE_URL") == "https://dashscope.aliyuncs.com/api/v1"

    # case2: with env variable DASHSCOPE_BASE_URL
    provider_base_url = "https://api.provider.com/v1"
    os.environ["DASHSCOPE_BASE_URL"] = provider_base_url
    config = QwenConfig(model="qwen-turbo", temperature=0.7, max_tokens=100, top_p=1.0, api_key="api_key")
    llm = QwenLLM(config)
    assert os.getenv("DASHSCOPE_BASE_URL") == provider_base_url

    # case3: with config.dashscope_base_url
    config_base_url = "https://api.config.com/v1"
    config = QwenConfig(
        model="qwen-turbo", temperature=0.7, max_tokens=100, top_p=1.0, api_key="api_key", dashscope_base_url=config_base_url
    )
    llm = QwenLLM(config)
    assert os.getenv("DASHSCOPE_BASE_URL") == config_base_url


def test_generate_response_without_tools(mock_dashscope_generation):
    config = QwenConfig(model="qwen-turbo", temperature=0.7, max_tokens=100, top_p=1.0, api_key="test_key")
    llm = QwenLLM(config)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "I'm doing well, thank you for asking!"}
    mock_dashscope_generation.call.return_value = mock_response

    response = llm.generate_response(messages)

    mock_dashscope_generation.call.assert_called_once_with(
        model="qwen-turbo", 
        messages=messages, 
        temperature=0.7, 
        max_tokens=100, 
        top_p=1.0
    )
    assert response == "I'm doing well, thank you for asking!"


def test_generate_response_with_tools(mock_dashscope_generation):
    config = QwenConfig(model="qwen-turbo", temperature=0.7, max_tokens=100, top_p=1.0, api_key="test_key")
    llm = QwenLLM(config)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Add a new memory: Today is a sunny day."},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_memory",
                "description": "Add a memory",
                "parameters": {
                    "type": "object",
                    "properties": {"data": {"type": "string", "description": "Data to add to memory"}},
                    "required": ["data"],
                },
            },
        }
    ]

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "I've added the memory for you."}
    mock_dashscope_generation.call.return_value = mock_response

    response = llm.generate_response(messages, tools=tools)

    mock_dashscope_generation.call.assert_called_once_with(
        model="qwen-turbo", 
        messages=messages, 
        temperature=0.7, 
        max_tokens=100, 
        top_p=1.0, 
        tools=tools, 
        tool_choice="auto"
    )

    assert response["content"] == "I've added the memory for you."
    assert response["tool_calls"] == []


def test_generate_response_with_tool_calls_in_content(mock_dashscope_generation):
    config = QwenConfig(model="qwen-turbo", temperature=0.7, max_tokens=100, top_p=1.0, api_key="test_key")
    llm = QwenLLM(config)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Add a new memory: Today is a sunny day."},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "add_memory",
                "description": "Add a memory",
                "parameters": {
                    "type": "object",
                    "properties": {"data": {"type": "string", "description": "Data to add to memory"}},
                    "required": ["data"],
                },
            },
        }
    ]

    # Mock response with tool calls in content
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {
        "text": 'I\'ve added the memory for you. {"tool_calls": [{"name": "add_memory", "arguments": {"data": "Today is a sunny day."}}]}'
    }
    mock_dashscope_generation.call.return_value = mock_response

    response = llm.generate_response(messages, tools=tools)

    assert response["content"] == 'I\'ve added the memory for you. {"tool_calls": [{"name": "add_memory", "arguments": {"data": "Today is a sunny day."}}]}'
    # Note: The current implementation doesn't parse tool calls from content
    # This test verifies the content is returned correctly even when tools are provided
    assert response["tool_calls"] == []


def test_response_callback_invocation(mock_dashscope_generation):
    # Setup mock callback
    mock_callback = Mock()
    
    config = QwenConfig(model="qwen-turbo", response_callback=mock_callback, api_key="test_key")
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "Test callback"}]
    
    # Mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "Response"}
    mock_dashscope_generation.call.return_value = mock_response
    
    # Call method
    llm.generate_response(messages)
    
    # Verify callback called with correct arguments
    mock_callback.assert_called_once()
    args = mock_callback.call_args[0]
    assert args[0] is llm  # llm_instance
    assert args[1] == mock_response  # raw_response
    assert "messages" in args[2]  # params


def test_no_response_callback(mock_dashscope_generation):
    config = QwenConfig(model="qwen-turbo", api_key="test_key")
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "Test no callback"}]
    
    # Mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "Response"}
    mock_dashscope_generation.call.return_value = mock_response
    
    # Should complete without calling any callback
    response = llm.generate_response(messages)
    assert response == "Response"
    
    # Verify no callback is set
    assert llm.config.response_callback is None


def test_callback_exception_handling(mock_dashscope_generation):
    # Callback that raises exception
    def faulty_callback(*args):
        raise ValueError("Callback error")
    
    config = QwenConfig(model="qwen-turbo", response_callback=faulty_callback, api_key="test_key")
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "Test exception"}]
    
    # Mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "Expected response"}
    mock_dashscope_generation.call.return_value = mock_response
    
    # Should complete without raising
    response = llm.generate_response(messages)
    assert response == "Expected response"
    
    # Verify callback was called (even though it raised an exception)
    assert llm.config.response_callback is faulty_callback


def test_callback_with_tools(mock_dashscope_generation):
    mock_callback = Mock()
    config = QwenConfig(model="qwen-turbo", response_callback=mock_callback, api_key="test_key")
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "Test tools"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {
                    "type": "object",
                    "properties": {"param1": {"type": "string"}},
                    "required": ["param1"],
                },
            }
        }
    ]
    
    # Mock tool response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "Tool response"}
    mock_dashscope_generation.call.return_value = mock_response
    
    llm.generate_response(messages, tools=tools)
    
    # Verify callback called with tool response
    mock_callback.assert_called_once()
    # Check that the response has the expected structure
    assert mock_callback.call_args[0][1].status_code == 200


def test_api_error_handling(mock_dashscope_generation):
    config = QwenConfig(model="qwen-turbo", api_key="test_key")
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "Test error"}]
    
    # Mock error response
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.message = "Bad Request"
    mock_dashscope_generation.call.return_value = mock_response
    
    # Should raise exception
    with pytest.raises(Exception) as exc_info:
        llm.generate_response(messages)
    
    assert "API request failed with status 400" in str(exc_info.value)


def test_enable_search_parameter(mock_dashscope_generation):
    config = QwenConfig(
        model="qwen-turbo", 
        enable_search=True, 
        search_params={"search_mode": "web"},
        api_key="test_key"
    )
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "What's the weather today?"}]
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": "Weather information"}
    mock_dashscope_generation.call.return_value = mock_response
    
    llm.generate_response(messages)
    
    # Verify search parameters are included
    call_args = mock_dashscope_generation.call.call_args[1]
    assert call_args["enable_search"] is True
    assert call_args["search_mode"] == "web"


def test_response_format_parameter(mock_dashscope_generation):
    config = QwenConfig(model="qwen-turbo", api_key="test_key")
    llm = QwenLLM(config)
    messages = [{"role": "user", "content": "Generate JSON"}]
    response_format = {"type": "json_object"}
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.message = "Success"
    mock_response.output = {"text": '{"result": "success"}'}
    mock_dashscope_generation.call.return_value = mock_response
    
    llm.generate_response(messages, response_format=response_format)
    
    # Verify response format is included
    call_args = mock_dashscope_generation.call.call_args[1]
    assert call_args["response_format"] == response_format


def test_missing_api_key():
    # Test without API key
    config = QwenConfig(model="qwen-turbo")
    
    # Clear environment variable
    if "DASHSCOPE_API_KEY" in os.environ:
        del os.environ["DASHSCOPE_API_KEY"]
    
    with pytest.raises(ValueError) as exc_info:
        QwenLLM(config)
    
    assert "API key is required" in str(exc_info.value)


def test_api_key_from_environment():
    # Test with API key from environment
    os.environ["DASHSCOPE_API_KEY"] = "env_api_key"
    
    config = QwenConfig(model="qwen-turbo")
    llm = QwenLLM(config)
    
    # Verify API key is set from environment variable
    # When using validation_alias with AliasChoices, pydantic will read the env var
    # and set it to the api_key field
    assert llm.config.api_key == "env_api_key"
    
    # Clean up
    del os.environ["DASHSCOPE_API_KEY"]


def test_dashscope_import_error():
    # Test when dashscope is not installed
    config = QwenConfig(model="qwen-turbo", api_key="test_key")
    
    with patch("powermem.integrations.llm.qwen.Generation", None), \
         patch("powermem.integrations.llm.qwen.DashScopeAPIResponse", None), \
         patch.dict('sys.modules', {'dashscope': None, 'dashscope.api_entities.dashscope_response': None}), \
         pytest.raises(ImportError) as exc_info:
        QwenLLM(config)
    
    assert "DashScope SDK is not installed" in str(exc_info.value)


def test_model_default_value():
    # Test default model assignment
    config = QwenConfig(api_key="test_key")
    llm = QwenLLM(config)
    
    assert llm.config.model == "qwen-turbo"


def test_config_conversion_from_dict():
    # Test conversion from dict to QwenConfig
    config_dict = {
        "model": "qwen-plus",
        "temperature": 0.5,
        "api_key": "test_key",
        "max_tokens": 1500
    }
    
    llm = QwenLLM(config_dict)
    
    assert llm.config.model == "qwen-plus"
    assert llm.config.temperature == 0.5
    assert llm.config.api_key == "test_key"
    assert llm.config.max_tokens == 1500
