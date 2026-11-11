"""命名场景：测试与演示共用的故障脚本。

装配逻辑（世界、后端、验证器、执行器）在 ``runtime/episode.py``；这里只负责
「按名字给出一组可复现的故障脚本与期望结果」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..runtime.episode import (
    DEFAULT_BOX_IDS,
    DEFAULT_ROBOTS,
    DEFAULT_SLOT_IDS,
    Episode,
    load_episode_config,
    make_episode,
    make_failures,
    make_images,
    make_verifier,
)

__all__ = [
    "DEFAULT_BOX_IDS",
    "DEFAULT_ROBOTS",
    "DEFAULT_SLOT_IDS",
    "Episode",
    "SCENARIOS",
    "Scenario",
    "get_scenario",
    "load_episode_config",
    "make_episode",
    "make_failures",
    "make_images",
    "make_verifier",
    "scenario_names",
]


# --------------------------------------------------------------------- 场景


@dataclass(frozen=True)
class Scenario:
    """一个命名场景：同时用于测试与演示。"""

    name: str
    description: str
    failure_script: Mapping[str, Sequence[Any]] | None = None
    failure_rate: float = 0.0
    verifier_kind: str = "oracle"
    verifier_reports: tuple[Mapping[str, Any], ...] | None = None
    expected_outcome: str = ""
    expected_sequence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "failure_script": {k: list(v) for k, v in (self.failure_script or {}).items()},
            "failure_rate": self.failure_rate,
            "verifier_kind": self.verifier_kind,
            "expected_outcome": self.expected_outcome,
        }


#: §5 要求的九个命名场景。
SCENARIOS: dict[str, Scenario] = {
    "happy": Scenario(
        name="happy",
        description="无故障：装满 → 验证 → 搬运 → 上架",
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "stuck_once": Scenario(
        name="stuck_once",
        description="install 卡住一次：reset robot1 → 补装 → 成功",
        failure_script={"robot1_install_screws": ["STUCK"]},
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "reset",
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "incomplete_topup": Scenario(
        name="incomplete_topup",
        description="install 装少了：验证发现不满 → 补装 → 成功",
        failure_script={"robot1_install_screws": [("INCOMPLETE", {"progress": 3})]},
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "stray_needs_human": Scenario(
        name="stray_needs_human",
        description="螺丝散落箱外：验证发现 → 求助人类 → 成功",
        failure_script={"robot1_install_screws": ["STRAY"]},
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "ask_human",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "carry_timeout_reset": Scenario(
        name="carry_timeout_reset",
        description="搬运超时：reset robot2 → 确认箱子在工位 → 重搬 → 成功",
        failure_script={"robot2_carry_box": [("TIMEOUT", {"lost": False})]},
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "reset",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "carry_silent_fail_detected": Scenario(
        name="carry_silent_fail_detected",
        description="搬运静默失败：POST_CHECK 发现箱子仍在工位 → 重搬 → 成功",
        failure_script={"robot2_carry_box": ["SILENT_FAIL"]},
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "lost_box_needs_human": Scenario(
        name="lost_box_needs_human",
        description="箱子丢失：reset → 发现箱子不在工位 → 求助 → 重新搬运 → 成功",
        failure_script={"robot2_carry_box": [("TIMEOUT", {"lost": True})]},
        expected_outcome="SUCCESS",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "reset",
            "verify_box",
            "ask_human",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
    "stuck_forever": Scenario(
        name="stuck_forever",
        description="install 一直卡住：限额耗尽 → 诚实放弃（SAFE_ABORT）",
        failure_script={"robot1_install_screws": ["STUCK"] * 10},
        expected_outcome="SAFE_ABORT",
        expected_sequence=(
            "robot1_install_screws",
            "reset",
            "robot1_install_screws",
            "reset",
            "robot1_install_screws",
            "reset",
            "robot1_install_screws",
            "ask_human",
            "verify_box",
            "robot1_install_screws",
            "ask_human",
            "verify_box",
            "robot1_install_screws",
            "finish",
        ),
    ),
    "verifier_false_positive": Scenario(
        name="verifier_false_positive",
        description="验证器误报完成：真值 N−1 却报 complete → 错误放行（FALSE_FINISH）",
        failure_script={"robot1_install_screws": ["STRAY"]},
        verifier_kind="scripted",
        verifier_reports=({"inside": 6, "outside": 0, "clear": True},),
        expected_outcome="FALSE_FINISH",
        expected_sequence=(
            "robot1_install_screws",
            "verify_box",
            "robot2_carry_box",
            "verify_box",
            "finish",
        ),
    ),
}


def get_scenario(name: str) -> Scenario:
    """按名字取场景；不存在时抛 KeyError。"""
    try:
        return SCENARIOS[name]
    except KeyError:
        raise KeyError(f"未知场景 '{name}'，可用场景：{sorted(SCENARIOS)}") from None


def scenario_names() -> list[str]:
    return sorted(SCENARIOS)
