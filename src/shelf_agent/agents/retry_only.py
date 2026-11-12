"""消融基线：只看 status 的重试策略。

**不验证、不求助**——只根据 ``status`` 判断「要重来」。它把「超时重试」与
「验证」两种机制分开：naive 什么都不做，retry_only 只做重试，rule 两者都做。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from ..contracts.types import EpisodeInfo, ToolCall, ToolResult

#: 限额默认值（与 ``configs/real_screw.yaml`` 的 rule_policy 段一致）。
DEFAULT_LIMITS: dict[str, int] = {
    "max_install_calls": 6,
    "max_resets_per_robot": 3,
    "max_carry_attempts": 3,
}


class RetryState(str, Enum):
    """retry_only 的状态。"""

    INSTALL = "INSTALL"
    RESET1 = "RESET1"
    CARRY = "CARRY"
    RESET2 = "RESET2"
    FINISH = "FINISH"


class RetryOnlyPolicy:
    """超时 → reset → 重试；成功就往下走。永不调用 verify / ask_human / inspector。"""

    name = "retry_only"

    def __init__(self, limits: Mapping[str, int] | None = None) -> None:
        self._limits = dict(DEFAULT_LIMITS)
        if limits:
            self._limits.update(limits)
        self._state = RetryState.INSTALL
        self._processed = 0
        self._finish_args: dict[str, Any] = {"success": False, "summary": "未完成"}
        self._n_install = 0
        self._n_carry = 0
        self._n_resets: dict[str, int] = {"robot1": 0, "robot2": 0}

    # ------------------------------------------------------------------ 协议

    def start(self, info: EpisodeInfo) -> None:
        self._state = RetryState.INSTALL
        self._processed = 0
        self._n_install = 0
        self._n_carry = 0
        self._n_resets = {"robot1": 0, "robot2": 0}
        self._finish_args = {"success": False, "summary": "未完成"}
        if info.limits:
            self._limits.update(info.limits)

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        self._consume(history)
        return self._current_call()

    # -------------------------------------------------------------- 状态推进

    def _consume(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> None:
        """处理历史里新增的 (调用, 结果)。"""
        for index in range(self._processed, len(history)):
            call, result = history[index]
            self._handle(call, result)
        self._processed = len(history)

    def _handle(self, call: ToolCall, result: ToolResult) -> None:
        """只看 status：timeout 就 reset 重试，done 就往下走。"""
        if call.name == "robot1_install_screws":
            self._n_install += 1
            if result.status == "timeout":
                if self._n_resets["robot1"] < self._limit("max_resets_per_robot"):
                    self._state = RetryState.RESET1
                else:
                    self._finish(False, "robot1 反复超时，reset 次数已用尽")
            elif result.status == "done":
                self._state = RetryState.CARRY
            else:
                self._finish(False, "install 调用失败")
            return
        if call.name == "robot2_carry_box":
            self._n_carry += 1
            if result.status == "timeout":
                if self._n_resets["robot2"] < self._limit("max_resets_per_robot"):
                    self._state = RetryState.RESET2
                else:
                    self._finish(False, "robot2 反复超时，reset 次数已用尽")
            elif result.status == "done":
                self._finish(True, "搬运完成")
            else:
                self._finish(False, "carry 调用失败")
            return
        if call.name == "reset":
            robot = str(call.args.get("robot", ""))
            if robot in self._n_resets:
                self._n_resets[robot] += 1
            if not result.ok:
                self._finish(False, "reset 调用失败")
                return
            if self._state is RetryState.RESET1:
                self._state = RetryState.INSTALL
            elif self._state is RetryState.RESET2:
                self._state = RetryState.CARRY
            return

    def _current_call(self) -> ToolCall:
        state = self._state
        if state is RetryState.INSTALL:
            if self._n_install >= self._limit("max_install_calls"):
                self._finish(False, "补装次数已用尽")
                return ToolCall("finish", dict(self._finish_args))
            return ToolCall("robot1_install_screws")
        if state is RetryState.RESET1:
            return ToolCall("reset", {"robot": "robot1"})
        if state is RetryState.CARRY:
            if self._n_carry >= self._limit("max_carry_attempts"):
                self._finish(False, "搬运尝试次数已用尽")
                return ToolCall("finish", dict(self._finish_args))
            return ToolCall("robot2_carry_box")
        if state is RetryState.RESET2:
            return ToolCall("reset", {"robot": "robot2"})
        return ToolCall("finish", dict(self._finish_args))

    def _limit(self, name: str) -> int:
        return int(self._limits.get(name, DEFAULT_LIMITS.get(name, 0)))

    def _finish(self, success: bool, summary: str) -> None:
        self._state = RetryState.FINISH
        self._finish_args = {"success": success, "summary": summary}

    @property
    def state(self) -> RetryState:
        """当前状态（调试与测试用）。"""
        return self._state
