"""共享枚举词汇。

放在 L0 是因为 ``contracts``（L1）与 ``perception``（L1）同层互不 import，
但两者都需要同一套「状态 / 位置」词汇。
"""

from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    """工具返回状态。实验室技能只返回 done / timeout，其余用 ok / error。"""

    DONE = "done"
    TIMEOUT = "timeout"
    OK = "ok"
    ERROR = "error"


class BoxLocation(str, Enum):
    """箱子的位置。真实值，不对 agent 暴露。"""

    STATION = "STATION"
    SHELF = "SHELF"
    LOST = "LOST"


class RobotState(str, Enum):
    """机器人状态。真实值，不对 agent 暴露。"""

    OK = "OK"
    FAULT = "FAULT"
