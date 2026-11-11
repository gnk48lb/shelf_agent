"""工具执行器：agent 与 backend 之间的唯一通道。

职责：校验工具名与参数 → 检查 episode 状态与预算 → 调 backend → 校验
``data`` 键 → 记轨迹。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts.schema import ArgsError, validate_args, validate_result_data
from ..contracts.spec import ToolSpecSet
from ..contracts.types import (
    ERR_BUDGET_EXCEEDED,
    ERR_EPISODE_FINISHED,
    ERR_INVALID_ARGS,
    ERR_UNKNOWN_TOOL,
    ToolCall,
    ToolResult,
)
from ..env.backend import SkillBackend
from ..utils.clock import VirtualClock
from .budget import BudgetState
from .trajectory import Trajectory


class ToolExecutor:
    """把一次 :class:`ToolCall` 变成一次 :class:`ToolResult`。"""

    def __init__(
        self,
        backend: SkillBackend,
        spec: ToolSpecSet,
        budget: BudgetState,
        trajectory: Trajectory,
        clock: VirtualClock,
    ) -> None:
        self.backend = backend
        self.spec = spec
        self.budget = budget
        self.trajectory = trajectory
        self.clock = clock
        self.finished = False

    # ------------------------------------------------------------------ 主流程

    def execute(self, call: ToolCall) -> ToolResult:
        """执行一次调用并记轨迹。

        非法工具名 / 非法参数 / 预算拒绝都返回 ``error`` 且**不推进仿真时钟**。
        """
        self.budget.register_call()
        result = self._dispatch(call)
        self.trajectory.add(self.clock.time_s, call, result, self.backend.debug_truth())
        return result

    def _dispatch(self, call: ToolCall) -> ToolResult:
        if self.finished:
            return ToolResult(
                status="error",
                error_code=ERR_EPISODE_FINISHED,
                message="episode 已经结束，调用被拒绝",
                duration_s=0.0,
            )
        if call.name not in self.spec:
            self.budget.register_invalid()
            return ToolResult(
                status="error",
                error_code=ERR_UNKNOWN_TOOL,
                message=f"未知工具 '{call.name}'，可用工具：{sorted(self.spec.tools)}",
                duration_s=0.0,
            )
        spec = self.spec.get(call.name)
        try:
            args = validate_args(spec, call.args)
        except ArgsError as exc:
            self.budget.register_invalid()
            return ToolResult(
                status="error", error_code=ERR_INVALID_ARGS, message=str(exc), duration_s=0.0
            )
        if self.budget.would_reject(call.name):
            self.budget.register_rejection()
            return ToolResult(
                status="error",
                error_code=ERR_BUDGET_EXCEEDED,
                message=(
                    f"预算已用尽（软上限 {self.budget.soft_limit}，"
                    f"已拒绝 {self.budget.rejected_calls} 次），物理技能 '{call.name}' 不再执行"
                ),
                duration_s=0.0,
            )
        result = self.backend.invoke(call.name, args)
        validate_result_data(spec, result.data)
        if call.name == "finish":
            self.finished = True
        return result

    # ------------------------------------------------------------------ 便利方法

    @property
    def aborted(self) -> bool:
        """预算是否已硬停。"""
        return self.budget.aborted

    def history(self) -> list[tuple[ToolCall, ToolResult]]:
        """已执行的 (调用, 结果) 列表——策略的输入。"""
        return [(step.call, step.result) for step in self.trajectory.steps]

    def tool_counts(self) -> Mapping[str, int]:
        return self.trajectory.tool_counts()
