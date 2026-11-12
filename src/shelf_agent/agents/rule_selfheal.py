"""规则自愈策略：显式状态机。

流程（状态 + 兜底转移表见 :data:`FALLBACK_TRANSITIONS`）::

    INSTALL ──done──▶ VERIFY ──complete──▶ CARRY ──done──▶ POST_CHECK
       ▲                  ▲                                    │
       │                  │                        BOX_NOT_AT_STATION
    timeout            补装/重拍                                ▼
       │                  │                              INSPECT(可选)
    RESET1 ◀──────────────┘                                    │
       │                                                       ▼
       └──done──▶ INSTALL                                   FINISH(True)

    CARRY ──timeout──▶ RESET2 ──done──▶ CARRY_CHECK ──箱子仍在工位──▶ CARRY(重试)
                                                    └──箱子不在工位──▶ HUMAN

    VERIFY 里发现散落 / 看不清 / 报告自相矛盾 → HUMAN；HUMAN 之后一律回 VERIFY。

两条硬规则：

1. **验证器没报告 complete 就绝不搬箱子**——``CARRY`` 只能从一次「完整」的
   验证报告进入（``test_policies.py`` 用 property 测试守住）。
2. 任一限额耗尽 → 先求助人类（还有配额时），否则诚实 ``finish(False)``。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from ..contracts.types import EpisodeInfo, ToolCall, ToolResult
from ..perception.completion import Report, is_complete

#: 策略限额默认值（与 ``configs/real_screw.yaml`` 的 rule_policy 段一致）。
DEFAULT_LIMITS: dict[str, int] = {
    "max_install_calls": 6,
    "max_resets_per_robot": 3,
    "max_unclear_retries": 2,
    "max_carry_attempts": 3,
    "max_human_asks": 2,
}


class RuleState(str, Enum):
    """规则策略的状态枚举。"""

    INSTALL = "INSTALL"
    RESET1 = "RESET1"
    VERIFY = "VERIFY"
    CARRY = "CARRY"
    RESET2 = "RESET2"
    CARRY_CHECK = "CARRY_CHECK"
    POST_CHECK = "POST_CHECK"
    INSPECT = "INSPECT"
    HUMAN = "HUMAN"
    FINISH = "FINISH"


#: **兜底**转移表：状态 × 事件 → 下一状态。
#:
#: 注意：这**不是**唯一的状态来源。:meth:`RuleSelfHealPolicy._handle` 里先处理
#: 一批「特例」（限额守卫、前置违规、注入事件标注），只有没被特例吃掉的��件才
#: 查这张表。所以表里有些条目在当前配置下是**够不到**的（例如「reset 被预算
#: 拒绝」才会走 ``reset_error``）。表保持完整是为了不依赖「当前配置恰好够不到」
#: 这种偶然——真到那条分支时行为仍然正确。
#:
#: 想把它变成「唯一来源」需要把限额守卫也搬进表里，但那会动到策略行为，
#: 收益不抵风险，详见 ``docs/open_questions.md``。
FALLBACK_TRANSITIONS: dict[RuleState, dict[str, RuleState]] = {
    RuleState.INSTALL: {
        "install_done": RuleState.VERIFY,
        "install_timeout": RuleState.RESET1,
        "install_error": RuleState.FINISH,
    },
    RuleState.RESET1: {
        "reset_done": RuleState.INSTALL,
        "reset_error": RuleState.FINISH,
    },
    RuleState.VERIFY: {
        "verify_complete": RuleState.CARRY,
        "verify_unclear": RuleState.VERIFY,
        "verify_inside_gt_target": RuleState.VERIFY,
        "verify_outside": RuleState.HUMAN,
        "verify_incomplete": RuleState.INSTALL,
        "verify_box_not_at_station": RuleState.HUMAN,
        "verify_error": RuleState.HUMAN,
    },
    RuleState.CARRY: {
        "carry_done": RuleState.POST_CHECK,
        "carry_timeout": RuleState.RESET2,
        "carry_error": RuleState.FINISH,
    },
    RuleState.RESET2: {
        "reset_done": RuleState.CARRY_CHECK,
        "reset_error": RuleState.FINISH,
    },
    RuleState.CARRY_CHECK: {
        "verify_box_not_at_station": RuleState.HUMAN,
        "verify_complete": RuleState.CARRY,
        "verify_incomplete": RuleState.VERIFY,
        "verify_unclear": RuleState.VERIFY,
        "verify_inside_gt_target": RuleState.VERIFY,
        "verify_outside": RuleState.HUMAN,
        "verify_error": RuleState.HUMAN,
    },
    RuleState.POST_CHECK: {
        "verify_box_not_at_station": RuleState.INSPECT,
        "verify_complete": RuleState.CARRY,
        "verify_incomplete": RuleState.VERIFY,
        "verify_unclear": RuleState.VERIFY,
        "verify_inside_gt_target": RuleState.VERIFY,
        "verify_outside": RuleState.HUMAN,
        "verify_error": RuleState.HUMAN,
    },
    RuleState.INSPECT: {
        "inspector_on_shelf": RuleState.FINISH,
        "inspector_off_shelf": RuleState.HUMAN,
        "inspector_error": RuleState.HUMAN,
    },
    RuleState.HUMAN: {
        "human_ok": RuleState.VERIFY,
        "human_error": RuleState.FINISH,
    },
    RuleState.FINISH: {},
}


class RuleSelfHealPolicy:
    """超时 reset 重试、验证后搬运、散落求助的规则策略。"""

    name = "rule"

    def __init__(self, limits: Mapping[str, int] | None = None) -> None:
        self._limits = dict(DEFAULT_LIMITS)
        if limits:
            self._limits.update(limits)
        self._state = RuleState.INSTALL
        self._target = 6
        self._inspector_mode = "noop"
        self._processed = 0
        self._question = "请检查一下工位"
        self._finish_args: dict[str, Any] = {"success": False, "summary": "未完成"}
        self._n_install = 0
        self._n_resets: dict[str, int] = {"robot1": 0, "robot2": 0}
        self._n_unclear = 0
        self._n_carry = 0
        self._n_human = 0

    # ------------------------------------------------------------------ 协议

    def start(self, info: EpisodeInfo) -> None:
        self._state = RuleState.INSTALL
        self._target = info.target_screws
        self._inspector_mode = getattr(info, "inspector_mode", "noop")
        self._processed = 0
        self._question = "请检查一下工位"
        self._finish_args = {"success": False, "summary": "未完成"}
        self._n_install = 0
        self._n_resets = {"robot1": 0, "robot2": 0}
        self._n_unclear = 0
        self._n_carry = 0
        self._n_human = 0
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
        """把一次调用结果翻译成事件，再按转移表推进状态。"""
        event = self._event_for(call, result)
        self._count(call, result)
        if event is None:
            return
        if event in ("install_timeout",):
            if self._n_resets["robot1"] < self._limit("max_resets_per_robot"):
                self._state = RuleState.RESET1
            else:
                self._goto_human_or_finish("robot1 反复卡住，reset 次数已用尽")
            return
        if event in ("verify_unclear", "verify_inside_gt_target"):
            if self._n_unclear < self._limit("max_unclear_retries"):
                self._n_unclear += 1
                self._state = RuleState.VERIFY
            else:
                self._goto_human_or_finish("验证报告始终不可信")
            return
        if event == "verify_incomplete":
            if self._n_install < self._limit("max_install_calls"):
                self._state = RuleState.INSTALL
            else:
                self._goto_human_or_finish("补装次数已用尽")
            return
        if event == "verify_outside":
            self._goto_human_or_finish("箱子外有散落的螺丝，需要人放回箱内")
            return
        if event == "verify_box_not_at_station":
            if self._state is RuleState.POST_CHECK:
                if self._inspector_mode == "shelf_status":
                    self._state = RuleState.INSPECT
                else:
                    self._finish(True, "箱子已搬到货架")
            else:
                self._goto_human_or_finish("箱子不在工位，可能搬运途中丢失")
            return
        if event in ("carry_complete_retry",):
            return
        if event == "inspector_on_shelf":
            # 巡检确认箱子在架上 —— 这是唯一「有独立证据」的完工信号。
            self._finish(True, "巡检确认箱子已在货架上")
            return
        target = FALLBACK_TRANSITIONS.get(self._state, {}).get(event)
        if target is None:
            self._goto_human_or_finish(f"无法处理的状态：{self._state.value}/{event}")
            return
        if target is RuleState.HUMAN:
            self._goto_human_or_finish("需要人工介入")
            return
        if target is RuleState.CARRY and self._n_carry >= self._limit("max_carry_attempts"):
            self._goto_human_or_finish("搬运尝试次数已用尽")
            return
        self._state = target

    def _event_for(self, call: ToolCall, result: ToolResult) -> str | None:
        """按当前状态把调用结果归类成事件。"""
        state = self._state
        if state is RuleState.INSTALL:
            if result.status == "timeout":
                return "install_timeout"
            if result.status == "done":
                return "install_done"
            return "install_error"
        if state in (RuleState.RESET1, RuleState.RESET2):
            return "reset_done" if result.ok else "reset_error"
        if state in (RuleState.VERIFY, RuleState.CARRY_CHECK, RuleState.POST_CHECK):
            return self._verify_event(result)
        if state is RuleState.CARRY:
            if result.status == "timeout":
                return "carry_timeout"
            if result.status == "done":
                return "carry_done"
            return "carry_error"
        if state is RuleState.INSPECT:
            if not result.ok:
                return "inspector_error"
            return "inspector_on_shelf" if result.data.get("box_on_shelf") else "inspector_off_shelf"
        if state is RuleState.HUMAN:
            return "human_ok" if result.ok else "human_error"
        return None

    def _verify_event(self, result: ToolResult) -> str:
        """把验证结果归类。完成判据只有一处：perception.completion.is_complete。"""
        if result.error_code == "BOX_NOT_AT_STATION":
            return "verify_box_not_at_station"
        if not result.ok:
            return "verify_error"
        report = Report.from_dict(result.data)
        if not report.clear:
            return "verify_unclear"
        if report.inside > self._target:
            return "verify_inside_gt_target"
        if report.outside > 0:
            return "verify_outside"
        if is_complete(report, self._target):
            return "verify_complete"
        return "verify_incomplete"

    def _count(self, call: ToolCall, result: ToolResult) -> None:
        """累计调用计数（按已完成的调用计）。"""
        if call.name == "robot1_install_screws":
            self._n_install += 1
        elif call.name == "robot2_carry_box":
            self._n_carry += 1
        elif call.name == "reset":
            robot = str(call.args.get("robot", ""))
            if robot in ("robot1", "robot2"):
                self._n_resets[robot] += 1
            else:
                self._n_resets["robot1"] += 1
                self._n_resets["robot2"] += 1
        elif call.name == "ask_human":
            self._n_human += 1

    # -------------------------------------------------------------- 产出调用

    def _current_call(self) -> ToolCall:
        state = self._state
        if state is RuleState.INSTALL:
            return ToolCall("robot1_install_screws")
        if state is RuleState.RESET1:
            return ToolCall("reset", {"robot": "robot1"})
        if state is RuleState.VERIFY:
            return ToolCall("verify_box")
        if state is RuleState.CARRY:
            return ToolCall("robot2_carry_box")
        if state is RuleState.RESET2:
            return ToolCall("reset", {"robot": "robot2"})
        if state in (RuleState.CARRY_CHECK, RuleState.POST_CHECK):
            return ToolCall("verify_box")
        if state is RuleState.INSPECT:
            return ToolCall("inspector_run")
        if state is RuleState.HUMAN:
            return ToolCall("ask_human", {"question": self._question})
        return ToolCall("finish", dict(self._finish_args))

    # ------------------------------------------------------------------ 辅助

    def _limit(self, name: str) -> int:
        return int(self._limits.get(name, DEFAULT_LIMITS.get(name, 0)))

    def _goto_human_or_finish(self, reason: str) -> None:
        """限额耗尽时的收尾：有配额就求助人类，否则诚实失败。"""
        if self._n_human < self._limit("max_human_asks"):
            self._question = reason
            self._state = RuleState.HUMAN
        else:
            self._finish(False, reason)

    def _finish(self, success: bool, summary: str) -> None:
        self._state = RuleState.FINISH
        self._finish_args = {"success": success, "summary": summary}

    # ------------------------------------------------------------------ 观测

    @property
    def state(self) -> RuleState:
        """当前状态（调试与测试用）。"""
        return self._state
