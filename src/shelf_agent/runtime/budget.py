"""两级预算（软/硬）与无效调用上限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..config import BudgetConfig

#: 物理技能：超过软预算后不再执行。其余工具（verify / ask_human / finish）始终可用。
PHYSICAL_TOOLS = frozenset(
    {
        "robot1_install_screws",
        "robot2_carry_box",
        "reset",
        "inspector_run",
    }
)


@dataclass
class BudgetState:
    """预算的运行时状态。

    * ``total_calls`` —— 进入 executor 的所有调用（含被拒的、verify、ask_human、finish）。
    * ``invalid_calls`` —— 工具名或参数非法的调用数。
    * ``rejected_calls`` —— 因超过软预算而被拒的物理技能调用数。

    被拒 ``hard_extra`` 次之后 episode 以 ``ABORTED_BUDGET`` 终止。
    """

    soft_limit: int
    hard_extra: int
    max_invalid_calls: int
    total_calls: int = 0
    invalid_calls: int = 0
    rejected_calls: int = 0
    aborted: bool = False

    @classmethod
    def from_config(cls, config: BudgetConfig) -> "BudgetState":
        return cls(
            soft_limit=config.soft_limit,
            hard_extra=config.hard_extra,
            max_invalid_calls=config.max_invalid_calls,
        )

    @property
    def over_soft(self) -> bool:
        """调用总数是否已超过软上限。"""
        return self.total_calls > self.soft_limit

    def register_call(self) -> int:
        """登记一次调用，返回登记后的总调用数。"""
        self.total_calls += 1
        return self.total_calls

    def register_invalid(self) -> None:
        """登记一次非法调用，并检查是否触发硬停。"""
        self.invalid_calls += 1
        if self.invalid_calls > self.max_invalid_calls:
            self.aborted = True

    def would_reject(self, tool: str) -> bool:
        """该工具此刻是否会被预算拒绝。"""
        return self.over_soft and tool in PHYSICAL_TOOLS

    def register_rejection(self) -> None:
        """登记一次被预算拒绝的调用，并检查是否触发硬停。"""
        self.rejected_calls += 1
        if self.rejected_calls >= self.hard_extra:
            self.aborted = True

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "soft_limit": self.soft_limit,
            "hard_extra": self.hard_extra,
            "max_invalid_calls": self.max_invalid_calls,
            "total_calls": self.total_calls,
            "invalid_calls": self.invalid_calls,
            "rejected_calls": self.rejected_calls,
            "aborted": self.aborted,
        }


def is_physical(tool: str, extra: Iterable[str] = ()) -> bool:
    """工具是否属于物理技能。"""
    return tool in PHYSICAL_TOOLS or tool in set(extra)
