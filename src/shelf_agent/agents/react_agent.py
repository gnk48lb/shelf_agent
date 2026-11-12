"""ReAct agent：用 tool calling 驱动同一套工具。

与规则策略共用 :class:`~shelf_agent.runtime.executor.ToolExecutor`——agent 侧
永远只经 executor 与环境交互。完整 messages（OpenAI chat 格式）与 tools schema
会一起进入轨迹 JSONL，将来可直接当 SFT 数据（``truth_snapshot`` 已严格剥离）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from ..contracts.schema import to_openai_tools
from ..contracts.types import EpisodeInfo, ToolCall, ToolResult
from .llm_client import LLMClient, OpenAICompatClient
from .prompts import DEFAULT_EXPOSED_TOOLS, build_system_prompt

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """agent 无法继续（连续无有效 tool call）。该 episode 记 ``AGENT_ERROR``。"""


#: 没有 tool call 时追加给模型的提醒。
NO_TOOL_CALL_REMINDER = (
    "Your last reply contained no tool call. Reply with exactly one tool call "
    "from the tools listed above."
)


class ReActAgent:
    """基于 LLM 的 ReAct 策略。"""

    name = "react"

    def __init__(
        self,
        client: LLMClient,
        tool_names: Sequence[str] | None = None,
        max_no_tool_call: int = 2,
        expose_head_image: bool = False,
    ) -> None:
        self.client = client
        self.tool_names = list(tool_names) if tool_names is not None else list(DEFAULT_EXPOSED_TOOLS)
        if expose_head_image and "robot1_get_head_image" not in self.tool_names:
            self.tool_names.append("robot1_get_head_image")
        self.max_no_tool_call = max_no_tool_call
        self._messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._issued_ids: list[str] = []
        self._consumed = 0
        self._no_call_streak = 0

    # ------------------------------------------------------------------ 协议

    def start(self, info: EpisodeInfo) -> None:
        """开局：生成 system prompt 与 tools schema。"""
        names = list(self.tool_names)
        # 巡检机器人能看货架时（inspector.mode = shelf_status）才暴露 inspector_run。
        if info.inspector_mode == "shelf_status" and "inspector_run" not in names:
            names.append("inspector_run")
        self._messages = [
            {"role": "system", "content": build_system_prompt(info, names)}
        ]
        selected = [t for t in info.tools if t.name in set(names)]
        self._tools = to_openai_tools(selected)
        self._issued_ids = []
        self._consumed = 0
        self._no_call_streak = 0

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        """问 LLM 要下一次调用。"""
        self._append_results(history)
        response = self.client.chat(self._messages, self._tools)
        if not response.tool_calls:
            self._no_call_streak += 1
            self._messages.append({"role": "assistant", "content": response.content})
            self._messages.append({"role": "user", "content": NO_TOOL_CALL_REMINDER})
            logger.warning("LLM 没有给出 tool call（第 %s 次）", self._no_call_streak)
            if self._no_call_streak >= self.max_no_tool_call:
                raise AgentError(
                    f"连续 {self.max_no_tool_call} 次没有有效 tool call，episode 记 AGENT_ERROR"
                )
            return self.next_call(history)
        self._no_call_streak = 0
        if len(response.tool_calls) > 1:
            logger.warning(
                "LLM 一次给出了 %s 个 tool call，只执行第一个", len(response.tool_calls)
            )
        request = response.tool_calls[0]
        self._messages.append(request.to_assistant_message())
        call_id = request.id or f"call_{request.name}"
        self._issued_ids.append(call_id)
        return ToolCall(request.name, dict(request.arguments))

    # ------------------------------------------------------------------ 辅助

    def _append_results(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> None:
        """把上一步的工具结果按 OpenAI chat 格式追加进 messages。"""
        for index in range(self._consumed, len(history)):
            call, result = history[index]
            call_id = self._issued_ids[index] if index < len(self._issued_ids) else f"call_{call.name}"
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": call.name,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
                }
            )
        self._consumed = len(history)

    @property
    def messages(self) -> list[dict[str, Any]]:
        """当前 messages（进轨迹用）。"""
        return [dict(m) for m in self._messages]

    @property
    def tools_schema(self) -> list[dict[str, Any]]:
        """当前 tools schema（进轨迹用）。"""
        return [dict(t) for t in self._tools]

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        tool_names: Sequence[str] | None = None,
        max_tokens: int | None = None,
    ) -> "ReActAgent":
        """用 ``config.LLMSettings`` 造一个走 OpenAI 兼容端点的 agent。"""
        client = OpenAICompatClient(
            base_url=settings.base_url,
            model=settings.model,
            api_key=settings.api_key,
            max_tokens=max_tokens,
        )
        return cls(client=client, tool_names=tool_names)
