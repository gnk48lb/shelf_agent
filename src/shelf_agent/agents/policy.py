"""策略协议。

策略只看得到工具调用与工具结果（``history``），以及开局时给的一份说明
（``info``）。真值对策略不可见。
"""

from __future__ import annotations

from typing import Protocol, Sequence

from ..contracts.types import EpisodeInfo, ToolCall, ToolResult


class Policy(Protocol):
    """确定性规则策略的协议。"""

    name: str

    def start(self, info: EpisodeInfo) -> None:
        """开局准备。``info`` 含工具规格、目标螺丝数 N 与策略限额。"""

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        """根据此前的 (调用, 结果) 列表给出下一次调用。"""
