"""世界状态：箱子、货架槽、机器人、求助计数。

真值都住在这里，只允许评测与测试通过 ``debug_truth()`` 读取。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts.types import BoxLocation, RobotState


@dataclass
class BoxState:
    """一个箱子的状态。"""

    box_id: str
    inside: int = 0
    outside: int = 0
    location: BoxLocation = BoxLocation.STATION

    def snapshot(self) -> dict[str, Any]:
        return {
            "inside": self.inside,
            "outside": self.outside,
            "location": self.location.value,
        }


@dataclass
class World:
    """整个仿真世界的状态。

    按多箱多槽设计：``boxes`` 里的箱子可以不止一个，``shelf_slots`` 里的槽位
    可以不止一个。当前 ``real_screw`` 配置只有 1 箱 1 槽。
    """

    boxes: dict[str, BoxState] = field(default_factory=dict)
    shelf_slots: dict[str, str] = field(default_factory=dict)
    robot_states: dict[str, RobotState] = field(default_factory=dict)
    human_asks: int = 0

    def box(self, box_id: str) -> BoxState:
        """取箱子；不存在时抛 KeyError。"""
        return self.boxes[box_id]

    def robot(self, name: str) -> RobotState:
        """取机器人状态。"""
        return self.robot_states.get(name, RobotState.OK)

    def set_robot(self, name: str, state: RobotState) -> None:
        self.robot_states[name] = state

    def place_on_shelf(self, box_id: str, preferred: str | None = None) -> str | None:
        """把箱子放进货架槽，返回槽位 id；没有空槽时返回 None。

        ``preferred`` 是期望的槽位：它空着就用它，被别的箱子占了就换另一个空槽
        （§6 的「槽位被占时换槽」）。槽位非空即视为被占——包括被本箱子占着
        （箱子既然在工位上，就不该还占着槽位）。
        """
        if preferred and preferred in self.shelf_slots and not self.shelf_slots[preferred]:
            self.shelf_slots[preferred] = box_id
            return preferred
        for slot_id, occupied in self.shelf_slots.items():
            if not occupied:
                self.shelf_slots[slot_id] = box_id
                return slot_id
        return None

    def release_slot(self, box_id: str) -> None:
        """箱子离开货架时腾出它占的槽位。"""
        for slot_id, occupied in list(self.shelf_slots.items()):
            if occupied == box_id:
                self.shelf_slots[slot_id] = ""

    def debug_truth(self) -> dict[str, Any]:
        """真值快照（仅供评测与测试）。"""
        return {
            "boxes": {bid: box.snapshot() for bid, box in sorted(self.boxes.items())},
            "robots": {name: state.value for name, state in sorted(self.robot_states.items())},
            "shelf_slots": dict(sorted(self.shelf_slots.items())),
            "human_asks": self.human_asks,
        }


def make_world(box_ids: list[str], slot_ids: list[str], robots: list[str]) -> World:
    """按盒子与槽位清单造一个世界。"""
    world = World(
        boxes={bid: BoxState(box_id=bid) for bid in box_ids},
        shelf_slots={sid: "" for sid in slot_ids},
        robot_states={name: RobotState.OK for name in robots},
    )
    return world
