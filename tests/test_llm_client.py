"""LLM 客户端单测：请求体、tool_calls 解析、退避重试、key 不泄漏。"""

from __future__ import annotations

import json
import logging

import pytest

from shelf_agent.agents.llm_client import (
    LLMResponse,
    OpenAICompatClient,
    ScriptedLLM,
    ToolCallRequest,
    TransportError,
    parse_openai_response,
)


def _response_with_tool(name: str = "verify_box", arguments: str = "{}") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            }
        ]
    }


class FakeTransport:
    """记录请求，按预设返回结果或抛错。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[tuple[dict, str, dict]] = []

    def __call__(self, payload, url, headers):
        self.calls.append((dict(payload), url, dict(headers)))
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def test_request_body_structure() -> None:
    transport = FakeTransport([_response_with_tool()])
    client = OpenAICompatClient(
        base_url="http://127.0.0.1:3425/v1",
        model="test-model",
        api_key="local",
        transport=transport,
    )
    messages = [{"role": "system", "content": "you are a controller"}]
    tools = [{"type": "function", "function": {"name": "verify_box"}}]
    client.chat(messages, tools)
    payload, url, headers = transport.calls[0]
    assert url == "http://127.0.0.1:3425/v1/chat/completions"
    assert payload["model"] == "test-model"
    assert payload["tool_choice"] == "auto"
    assert payload["temperature"] == 0.0
    assert payload["messages"] == messages
    assert payload["tools"] == tools
    assert headers["Authorization"] == "Bearer local"
    assert headers["Content-Type"] == "application/json"


def test_max_tokens_is_optional() -> None:
    transport = FakeTransport([_response_with_tool()])
    client = OpenAICompatClient(
        base_url="http://x/v1", model="m", api_key="k", transport=transport, max_tokens=1024
    )
    client.chat([{"role": "user", "content": "hi"}], [])
    assert transport.calls[0][0]["max_tokens"] == 1024
    transport2 = FakeTransport([_response_with_tool()])
    client2 = OpenAICompatClient(base_url="http://x/v1", model="m", api_key="k", transport=transport2)
    client2.chat([{"role": "user", "content": "hi"}], [])
    assert "max_tokens" not in transport2.calls[0][0]


def test_tool_calls_are_parsed() -> None:
    transport = FakeTransport([_response_with_tool("reset", '{"robot": "robot1"}')])
    client = OpenAICompatClient(base_url="http://x/v1", model="m", api_key="k", transport=transport)
    response = client.chat([{"role": "user", "content": "go"}], [])
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "reset"
    assert call.arguments == {"robot": "robot1"}
    assert call.id == "call_1"


def test_invalid_json_arguments_fall_back_to_empty() -> None:
    transport = FakeTransport([_response_with_tool("reset", "not json")])
    client = OpenAICompatClient(base_url="http://x/v1", model="m", api_key="k", transport=transport)
    response = client.chat([{"role": "user", "content": "go"}], [])
    assert response.tool_calls[0].arguments == {}


def test_plain_text_response() -> None:
    data = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
    transport = FakeTransport([data])
    client = OpenAICompatClient(base_url="http://x/v1", model="m", api_key="k", transport=transport)
    response = client.chat([{"role": "user", "content": "go"}], [])
    assert response.content == "hello"
    assert response.tool_calls == []


def test_empty_choices_gives_empty_response() -> None:
    assert parse_openai_response({}).content == ""


def test_backoff_on_429_and_503() -> None:
    """429 / 5xx 走指数退避，sleep 可注入以便断言间隔序列。"""
    slept: list[float] = []
    transport = FakeTransport(
        [
            TransportError("HTTP 429", status=429, retryable=True),
            TransportError("HTTP 503", status=503, retryable=True),
            _response_with_tool(),
        ]
    )
    client = OpenAICompatClient(
        base_url="http://x/v1",
        model="m",
        api_key="k",
        transport=transport,
        backoff_base_s=0.5,
        sleep=slept.append,
    )
    response = client.chat([{"role": "user", "content": "go"}], [])
    assert response.tool_calls
    assert slept == [0.5, 1.0]
    assert len(transport.calls) == 3


def test_retries_are_bounded() -> None:
    slept: list[float] = []
    transport = FakeTransport([TransportError("HTTP 500", status=500, retryable=True)])
    client = OpenAICompatClient(
        base_url="http://x/v1",
        model="m",
        api_key="k",
        transport=transport,
        max_retries=2,
        backoff_base_s=0.1,
        sleep=slept.append,
    )
    with pytest.raises(TransportError):
        client.chat([{"role": "user", "content": "go"}], [])
    assert len(transport.calls) == 3  # 首次 + 2 次重试
    assert slept == [0.1, 0.2]


def test_non_retryable_error_is_not_retried() -> None:
    transport = FakeTransport([TransportError("HTTP 400", status=400, retryable=False)])
    client = OpenAICompatClient(base_url="http://x/v1", model="m", api_key="k", transport=transport)
    with pytest.raises(TransportError):
        client.chat([{"role": "user", "content": "go"}], [])
    assert len(transport.calls) == 1


def test_api_key_never_appears_in_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    transport = FakeTransport([TransportError("HTTP 400", status=400, retryable=False)])
    client = OpenAICompatClient(
        base_url="http://x/v1", model="m", api_key="super-secret-key", transport=transport
    )
    with pytest.raises(TransportError) as excinfo:
        client.chat([{"role": "user", "content": "go"}], [])
    assert "super-secret-key" not in str(excinfo.value)
    for record in caplog.records:
        assert "super-secret-key" not in record.getMessage()


def test_api_key_never_appears_in_exception_messages() -> None:
    transport = FakeTransport([TransportError("HTTP 400", status=400, retryable=False)])
    client = OpenAICompatClient(
        base_url="http://x/v1", model="m", api_key="sk-do-not-leak", transport=transport
    )
    try:
        client.chat([{"role": "user", "content": "go"}], [])
    except TransportError as exc:
        assert "sk-do-not-leak" not in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当抛出 TransportError")


def test_scripted_llm_returns_responses_in_order() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(content="a", tool_calls=[ToolCallRequest("verify_box", {}, "c1")]),
            LLMResponse(content="b", tool_calls=[ToolCallRequest("finish", {"success": True}, "c2")]),
        ]
    )
    first = llm.chat([], [])
    assert first.tool_calls[0].name == "verify_box"
    second = llm.chat([], [])
    assert second.tool_calls[0].name == "finish"
    assert llm.used == 2


def test_scripted_llm_supports_callbacks() -> None:
    def callback(messages):
        return LLMResponse(content="", tool_calls=[ToolCallRequest("verify_box", {}, "x")])

    llm = ScriptedLLM([callback])
    assert llm.chat([], []).tool_calls[0].name == "verify_box"


def test_assistant_message_roundtrip() -> None:
    request = ToolCallRequest("reset", {"robot": "robot1"}, "call_9")
    message = request.to_assistant_message()
    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["function"]["name"] == "reset"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"robot": "robot1"}
