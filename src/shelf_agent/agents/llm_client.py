"""LLM 客户端。

* :class:`LLMClient` —— 协议：``chat(messages, tools) -> LLMResponse``。
* :class:`ScriptedLLM` —— 按脚本返回响应（测试用，不联网）。
* :class:`OpenAICompatClient` —— 基于标准库 ``urllib`` 的 OpenAI 兼容端点。

**硬约束**（§3.7）：不硬编码任何服务地址；API key 只来自配置 / 环境变量，且不得出现在
日志或异常信息里；``sleep`` 与 ``transport`` 均可注入，便于离线测试。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCallRequest:
    """LLM 请求的一次工具调用。``arguments`` 是已经解析好的参数字典。"""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    id: str = ""

    def to_assistant_message(self) -> dict[str, Any]:
        """转成 OpenAI chat 格式的 assistant 消息。"""
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": self.id or f"call_{self.name}",
                    "type": "function",
                    "function": {
                        "name": self.name,
                        "arguments": json.dumps(dict(self.arguments), ensure_ascii=False),
                    },
                }
            ],
        }


@dataclass(frozen=True)
class LLMResponse:
    """一次 LLM 响应。"""

    content: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


class LLMClient(Protocol):
    """LLM 客户端协议。"""

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        """发一轮对话，返回响应。"""


class ScriptedLLM:
    """脚本化 LLM：按顺序返回预设响应；也可以用回调动态生成。"""

    def __init__(
        self,
        responses: Sequence[LLMResponse | Callable[[Sequence[Mapping[str, Any]]], LLMResponse]],
    ) -> None:
        self._responses = list(responses)
        self._index = 0
        self.calls: list[list[Mapping[str, Any]]] = []

    @property
    def used(self) -> int:
        """已被消费的响应数。"""
        return self._index

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise ValueError("ScriptedLLM 没有配置任何响应")
        item = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        if callable(item):
            return item(list(messages))
        return item


class TransportError(RuntimeError):
    """HTTP 传输错误。``retryable`` 表示值得退避重试。"""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


#: 传输函数的签名：(payload, url, headers) -> 响应体 dict。
Transport = Callable[[Mapping[str, Any], str, Mapping[str, str]], Mapping[str, Any]]


def urllib_transport(
    payload: Mapping[str, Any],
    url: str,
    headers: Mapping[str, str],
    timeout_s: float = 60.0,
) -> Mapping[str, Any]:
    """默认传输：用标准库 ``urllib`` 发 POST。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
        raise TransportError(
            f"HTTP {status}: {body[:200]}", status=status, retryable=(status == 429 or status >= 500)
        ) from None
    except urllib.error.URLError as exc:
        raise TransportError(f"网络错误：{exc.reason}", status=None, retryable=True) from None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise TransportError("响应不是合法 JSON", status=None, retryable=False) from None
    if not isinstance(parsed, dict):
        raise TransportError("响应顶层不是对象", status=None, retryable=False)
    return parsed


class OpenAICompatClient:
    """OpenAI 兼容端点客户端（``POST {base_url}/chat/completions``）。

    * ``base_url`` 必须自带 ``/v1``（客户端自己拼 ``/chat/completions``）；
    * 429 / 5xx 走指数退避重试，退避的 ``sleep`` 可注入；
    * API key 只放进请求头，不进日志、不进异常信息。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        backoff_base_s: float = 0.5,
        sleep: Callable[[float], None] | None = None,
        transport: Transport | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep if sleep is not None else time.sleep
        self._transport = transport
        self.max_tokens = max_tokens
        self.temperature = temperature

    # ------------------------------------------------------------------ 内部

    @property
    def url(self) -> str:
        """请求地址。"""
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _backoff(self, attempt: int) -> float:
        """第 ``attempt`` 次重试前的等待秒数（指数退避）。"""
        return self.backoff_base_s * (2 ** (attempt - 1))

    def _build_payload(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "tool_choice": "auto",
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = [dict(t) for t in tools]
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload

    # ------------------------------------------------------------------ 对外

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> LLMResponse:
        """发一轮对话。遇到 429/5xx 会退避重试。"""
        payload = self._build_payload(messages, tools)
        url = self.url
        headers = self._headers()
        attempt = 0
        while True:
            try:
                if self._transport is not None:
                    data = self._transport(payload, url, headers)
                else:
                    data = urllib_transport(payload, url, headers, self.timeout_s)
            except TransportError as exc:
                attempt += 1
                if not exc.retryable or attempt > self.max_retries:
                    raise
                delay = self._backoff(attempt)
                logger.warning(
                    "LLM 请求失败（status=%s），第 %s 次退避 %.2fs 后重试", exc.status, attempt, delay
                )
                self._sleep(delay)
                continue
            return parse_openai_response(data)


def parse_openai_response(data: Mapping[str, Any]) -> LLMResponse:
    """解析 ``/chat/completions`` 的响应，取 ``tool_calls[*].function``。"""
    choices = data.get("choices") or []
    if not choices:
        return LLMResponse(content="")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    tool_calls: list[ToolCallRequest] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        raw_args = function.get("arguments") or "{}"
        if isinstance(raw_args, Mapping):
            arguments: dict[str, Any] = dict(raw_args)
        else:
            try:
                parsed = json.loads(raw_args)
                arguments = dict(parsed) if isinstance(parsed, Mapping) else {}
            except json.JSONDecodeError:
                logger.warning("工具调用的 arguments 不是合法 JSON，按空参数处理")
                arguments = {}
        tool_calls.append(
            ToolCallRequest(name=str(name), arguments=arguments, id=str(raw.get("id") or ""))
        )
    return LLMResponse(content=str(content), tool_calls=tool_calls)
