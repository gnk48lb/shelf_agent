"""L1 契约层共享类型：工具调用、工具结果、错误码。

这些类型是 agent、runtime 与 env 之间的公共词汇；真值字段刻意不出现在这里。

状态与位置枚举定义在 ``utils/enums.py``（L0）——因为 ``contracts`` 与
``perception`` 同层互不 import，但两者都要用同一套词汇。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..utils.enums import BoxLocation, RobotState, Status

__all__ = [
    "BoxLocation",
    "ContractViolation",
    "EpisodeInfo",
    "RobotState",
    "Status",
    "ToolCall",
    "ToolResult",
]

#: 错误码常量（§3.1 / §3.8）。
ERR_UNKNOWN_TOOL = "UNKNOWN_TOOL"
ERR_INVALID_ARGS = "INVALID_ARGS"
ERR_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
ERR_BOX_NOT_AT_STATION = "BOX_NOT_AT_STATION"
ERR_HUMAN_UNAVAILABLE = "HUMAN_UNAVAILABLE"
ERR_MAX_STEPS = "MAX_STEPS"
ERR_EPISODE_FINISHED = "EPISODE_FINISHED"


class ContractViolation(RuntimeError):
    """工具返回了契约里没有声明的 data 键，或结果结构与契约不符。"""


@dataclass(frozen=True)
class ToolCall:
    """一次工具调用请求。``args`` 的键必须与契约里的 params 对齐。"""

    name: str
    args: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": dict(self.args)}


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结果。

    ``data`` 的键必须在 YAML 契约的 ``returns_data`` 中声明过，否则 executor
    会抛 :class:`ContractViolation`。真值永远不出现在这里。
    """

    status: str
    error_code: str | None = None
    message: str = ""
    duration_s: float = 0.0
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """是否成功（done / ok）。"""
        return self.status in (Status.DONE.value, Status.OK.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error_code": self.error_code,
            "message": self.message,
            "duration_s": self.duration_s,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class EpisodeInfo:
    """交给策略与 LLM 的只读环境说明（不含任何真值）。"""

    scenario: str
    target_screws: int
    tools: tuple[Any, ...]
    limits: Mapping[str, int] = field(default_factory=dict)
    inspector_mode: str = "noop"
