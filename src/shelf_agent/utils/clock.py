"""虚拟时钟。

仿真里的一切耗时都记在这条时钟上，保证可复现且不依赖真实时间。
"""

from __future__ import annotations


class VirtualClock:
    """单调递增的虚拟时钟，单位秒。"""

    __slots__ = ("_time_s",)

    def __init__(self, start_s: float = 0.0) -> None:
        if start_s < 0:
            raise ValueError(f"起始时间不能为负：{start_s}")
        self._time_s = float(start_s)

    @property
    def time_s(self) -> float:
        """当前虚拟时刻（秒）。"""
        return self._time_s

    def advance(self, delta_s: float) -> float:
        """推进时钟并返回新的时刻。负数会抛 ValueError（保证单调不减）。"""
        if delta_s < 0:
            raise ValueError(f"虚拟时钟不能倒退：delta={delta_s}")
        self._time_s += float(delta_s)
        return self._time_s

    def reset(self) -> None:
        """回到起点（仅用于测试）。"""
        self._time_s = 0.0

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"VirtualClock(time_s={self._time_s:.3f})"
