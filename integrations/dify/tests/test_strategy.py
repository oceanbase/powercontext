import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from dify_plugin.entities.agent import AgentRuntime
from dify_plugin.entities.model.llm import LLMResultChunk
from dify_plugin.entities.tool import ToolInvokeMessage
from strategies.function_calling import PowerContextStrategy
from strategies.window import input_size


def tool(name, **extra):
    return {
        "identity": {"author": "test", "name": name, "provider": "example/tools", "label": {"en_US": name}},
        "description": {"human": {"en_US": name}, "llm": name},
        **extra,
    }


def message(value):
    return ToolInvokeMessage(type="json", message={"json_object": value})


@pytest.mark.parametrize("package", ["powercontext", "powercontext_agent"])
def test_package_loads_with_real_sdk_registration(package):
    root = Path(__file__).resolve().parents[1] / package
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from dify_plugin.config.config import DifyPluginEnv; from dify_plugin.core.plugin_registration import PluginRegistration; PluginRegistration(DifyPluginEnv())",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("unavailable", [False, True])
def test_sdk_strategy_runs_tools_and_automatically_records_trajectory(unavailable):
    captured = []
    model_requests = []

    def invoke_tool(**kwargs):
        if kwargs["tool_name"] == "lookup":
            assert kwargs["parameters"]["tenant"] == "configured"
            return iter([message({"answer": "Found"})])
        assert kwargs["parameters"]["memory_context"] == {
            "app_id": "app-1",
            "subject_kind": "user",
            "subject_id": "user-1",
        }
        if unavailable:
            return iter([message({"status": "error", "error": "server_unavailable"})])
        request = kwargs["parameters"]["request"]
        if kwargs["tool_name"] == "prepare_context":
            return iter([message({"status": "ready", "content": "Relevant fact", "content_bytes": 13})])
        captured.append(request)
        return iter([message({"status": "accepted"})])

    def invoke_model(**kwargs):
        assert kwargs["stream"] is True
        model_requests.append(kwargs)
        assert [tool.name for tool in kwargs["tools"]] == ["lookup"]
        assert input_size(kwargs["prompt_messages"][0].content, kwargs["prompt_messages"][1:], kwargs["tools"]) <= 25600
        response = {"role": "assistant", "content": "Done"}
        if len(model_requests) == 1:
            response = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"tenant":"forged"}'},
                    }
                ],
            }
        return iter([LLMResultChunk(model="test", delta={"index": 0, "message": response})])

    session = SimpleNamespace(
        app_id="app-1",
        message_id="message-1",
        tool=SimpleNamespace(invoke=invoke_tool),
        model=SimpleNamespace(llm=SimpleNamespace(invoke=invoke_model)),
    )
    strategy = PowerContextStrategy(AgentRuntime(user_id="user-1"), session)
    output = list(
        strategy.invoke({
            "model": {"provider": "test", "model": "test", "mode": "chat", "completion_params": {}},
            "tools": [
                tool("prepare_context"),
                tool("capture_event"),
                tool("lookup", runtime_parameters={"tenant": "configured"}),
            ],
            "query": "Help",
            "context_window": 32000,
        })
    )
    assert any(item.type.value == "text" and item.message.text == "Done" for item in output)
    if unavailable:
        assert any(item.type.value == "log" for item in output)
    else:
        assert [event["event"] for event in captured] == [
            "user_prompt",
            "tool_call",
            "tool_result",
            "model_response",
            "run_end",
        ]
        assert captured[-1]["payload"]["status"] == "succeeded"
        assert "Relevant fact" not in json.dumps(captured)


def test_context_compaction_preserves_first_user_and_complete_recent_tool_pairs():
    from dify_plugin.entities.model.llm import LLMModelConfig, LLMResult, LLMUsage
    from dify_plugin.entities.model.message import AssistantPromptMessage, ToolPromptMessage, UserPromptMessage
    from strategies.window import fit_window

    history = [UserPromptMessage(content="Original task")]
    for index in range(25):
        history.extend([
            AssistantPromptMessage(
                content="",
                tool_calls=[
                    {"id": f"call-{index}", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
                ],
            ),
            ToolPromptMessage(tool_call_id=f"call-{index}", content="x" * 1500),
        ])
    history.append(UserPromptMessage(content="Continue"))
    summary_requests = []

    def summarize(**kwargs):
        summary_requests.append(kwargs)
        assert kwargs["stream"] is False
        prompts = kwargs["prompt_messages"]
        assert input_size(prompts[0].content, prompts[1:], []) <= 12000
        return LLMResult(
            model="test",
            prompt_messages=prompts,
            message=AssistantPromptMessage(content="Progress summary"),
            usage=LLMUsage.empty_usage(),
        )

    session = SimpleNamespace(model=SimpleNamespace(llm=SimpleNamespace(invoke=summarize)))
    strategy = PowerContextStrategy(AgentRuntime(user_id="user"), session)
    result = fit_window(
        session,
        LLMModelConfig(provider="test", model="test", mode="chat", completion_params={}),
        "system",
        history,
        [],
        12000,
        {"usage": None},
        strategy,
    )
    assert summary_requests
    assert result[0].content == "Original task"
    assert result[-1].content == "Continue"
    assert input_size("system", result, []) <= 12000
    calls = {call.id for item in result if isinstance(item, AssistantPromptMessage) for call in item.tool_calls}
    returns = {item.tool_call_id for item in result if isinstance(item, ToolPromptMessage)}
    assert calls == returns
    assert history[2].content == "x" * 1500


def test_oversized_current_task_never_reaches_the_model():
    from dify_plugin.entities.model.llm import LLMModelConfig
    from dify_plugin.entities.model.message import UserPromptMessage
    from strategies.window import fit_window

    with pytest.raises(ValueError, match="exceed"):
        fit_window(
            None,
            LLMModelConfig(provider="test", model="test", mode="chat", completion_params={}),
            "system",
            [UserPromptMessage(content="中" * 2000)],
            [],
            1024,
            {},
            None,
        )
