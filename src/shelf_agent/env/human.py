"""人类求助通道。

仿真里由 :class:`ScriptedHuman` 顶替：人类会把箱外散落的螺丝放回箱内、
把丢失的箱子放回工位、把故障机器人复位。真人通道不在本期范围（见
``docs/open_questions.md`` Q11）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.types import ERR_HUMAN_UNAVAILABLE, BoxLocation, RobotState
from .world import World

#: 人类无事可做时的固定回答。
NOTHING_TO_DO = "未发现异常"


@dataclass(frozen=True)
class HumanAnswer:
    """一次求助的结果。"""

    status: str
    message: str
    answer: str = ""
    error_code: str | None = None
    duration_s: float = 0.0


class ScriptedHuman:
    """脚本化人类。配额用尽时返回 error（耗时 0）。"""

    def __init__(self, latency_s: float = 120.0, max_asks: int = 2) -> None:
        self.latency_s = float(latency_s)
        self.max_asks = int(max_asks)
        self._last_asks = 0

    @property
    def asks(self) -> int:
        """已发起的求助次数（由世界状态记录）。"""
        return self._last_asks

    def ask(self, question: str, world: World) -> HumanAnswer:
        """向人类求助。人类会顺手处理能处理的异常。"""
        if world.human_asks >= self.max_asks:
            return HumanAnswer(
                status="error",
                message="人类求助配额已用尽",
                error_code=ERR_HUMAN_UNAVAILABLE,
                duration_s=0.0,
            )
        world.human_asks += 1
        self._last_asks = world.human_asks
        actions: list[str] = []
        for box in world.boxes.values():
            if box.outside > 0:
                moved = box.outside
                box.inside += moved
                box.outside = 0
                actions.append(f"把箱外散落的 {moved} 颗螺丝放回箱内")
            if box.location is BoxLocation.LOST:
                box.location = BoxLocation.STATION
                actions.append(f"把丢失的箱子 {box.box_id} 放回工位")
        for name, state in sorted(world.robot_states.items()):
            if state is RobotState.FAULT:
                world.set_robot(name, RobotState.OK)
                actions.append(f"把 {name} 复位")
        if not actions:
            return HumanAnswer(
                status="ok",
                message="人类已查看，未发现异常",
                answer=NOTHING_TO_DO,
                duration_s=self.latency_s,
            )
        return HumanAnswer(
            status="ok",
            message="人类已处理异常",
            answer="；".join(actions),
            duration_s=self.latency_s,
        )
