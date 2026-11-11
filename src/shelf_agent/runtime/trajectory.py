"""轨迹记录。

每步记录 ``{i, t_s, call, result, truth_snapshot}``。``truth_snapshot`` 单独存放，
**不得进入 agent 的任何输入**（由 ``tests/test_no_leak.py`` 守住）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..contracts.types import ToolCall, ToolResult


@dataclass(frozen=True)
class Step:
    """一步轨迹。"""

    index: int
    t_s: float
    call: ToolCall
    result: ToolResult
    truth_snapshot: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "i": self.index,
            "t_s": round(self.t_s, 6),
            "call": self.call.to_dict(),
            "result": self.result.to_dict(),
            "truth_snapshot": dict(self.truth_snapshot),
        }


@dataclass
class Trajectory:
    """一整个 episode 的轨迹。"""

    steps: list[Step] = field(default_factory=list)

    def add(
        self,
        t_s: float,
        call: ToolCall,
        result: ToolResult,
        truth_snapshot: Mapping[str, Any] | None = None,
    ) -> Step:
        """追加一步并返回它。"""
        step = Step(
            index=len(self.steps),
            t_s=t_s,
            call=call,
            result=result,
            truth_snapshot=dict(truth_snapshot or {}),
        )
        self.steps.append(step)
        return step

    @property
    def calls(self) -> list[ToolCall]:
        return [step.call for step in self.steps]

    @property
    def results(self) -> list[ToolResult]:
        return [step.result for step in self.steps]

    def tool_counts(self) -> dict[str, int]:
        """各工具的调用次数。"""
        counts: dict[str, int] = {}
        for step in self.steps:
            counts[step.call.name] = counts.get(step.call.name, 0) + 1
        return counts

    def to_list(self) -> list[dict[str, Any]]:
        return [step.to_dict() for step in self.steps]
