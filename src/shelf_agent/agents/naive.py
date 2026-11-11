"""对照策略：不验证、不重试的直线脚本。

它只用来回答「自愈到底带来了多少」——如果裸做也会成功，那自愈流程就没意义。
"""

from __future__ import annotations

from typing import Sequence

from ..contracts.types import EpisodeInfo, ToolCall, ToolResult
from .policy import Policy


class NaiveScriptPolicy:
    """install → carry → finish(True)。"""

    name = "naive"

    def __init__(self) -> None:
        self._step = 0

    def start(self, info: EpisodeInfo) -> None:
        self._step = 0

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        index = len(history)
        if index == 0:
            self._step = 1
            return ToolCall("robot1_install_screws")
        if index == 1:
            self._step = 2
            return ToolCall("robot2_carry_box")
        self._step = 3
        return ToolCall("finish", {"success": True, "summary": "装好了，搬上去了"})
