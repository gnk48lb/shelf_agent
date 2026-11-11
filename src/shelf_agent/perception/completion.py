"""完成判据。

**只有这一处**定义「箱子装好了」：报告清晰、箱内正好 N 颗、箱外没有散落。
评测用的真值判据 :func:`truth_success` 与之对应，但看的是世界真实状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..utils.enums import BoxLocation


@dataclass(frozen=True)
class Report:
    """验证器给出的箱内状态报告。"""

    inside: int
    outside: int
    clear: bool

    def to_dict(self) -> dict[str, Any]:
        return {"inside": self.inside, "outside": self.outside, "clear": self.clear}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Report":
        return cls(
            inside=int(data["inside"]),
            outside=int(data["outside"]),
            clear=bool(data["clear"]),
        )


def is_complete(report: Report, target: int) -> bool:
    """唯一一处完成判据：清晰且 inside == N 且 outside == 0。"""
    return bool(report.clear) and report.inside == target and report.outside == 0


def truth_success(location: str, inside: int, outside: int, target: int) -> bool:
    """评测用的真值判据：箱子在货架上且箱内正好 N 颗、箱外无散落。"""
    return location == BoxLocation.SHELF.value and inside == target and outside == 0
