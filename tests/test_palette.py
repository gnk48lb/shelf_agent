"""出图配色的可分辨性守卫。

图上四条策略线必须**两两可辨**。这不是审美问题：旧配色里 `naive` 与 `retry_only`
的 OKLab ΔE 只有 4.1（常视觉门槛 15），在 CVD 模拟下更是 0.1——**完全分不开**，
组会上投影时观众根本认不出哪条是哪条。

这里用与色板校验脚本同一套 OKLab 公式（sRGB → 线性 → LMS → OKLab），
把「两两 ΔE ≥ 15」和「每个策略有独立标记形状」两条规则钉死，防止有人改回相近色。
"""

from __future__ import annotations

import math

import pytest

from shelf_agent.eval.report import POLICY_COLORS, POLICY_MARKERS

#: 常视觉下的最小可辨距离（OKLab ΔE ×100）。低于此值即使色觉正常也难分辨。
MIN_DELTA_E = 15.0


def _srgb_to_linear(channel: float) -> float:
    """sRGB 分量 → 线性光。"""
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _oklab(hex_color: str) -> tuple[float, float, float]:
    """十六进制 sRGB → OKLab（L, a, b）。"""
    raw = hex_color.lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"颜色必须是 6 位十六进制：{hex_color!r}")
    rgb = [_srgb_to_linear(int(raw[i : i + 2], 16) / 255.0) for i in (0, 2, 4)]
    r, g, b = rgb
    long_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    medium = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    short = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_ = math.copysign(abs(long_) ** (1 / 3), long_)
    m_ = math.copysign(abs(medium) ** (1 / 3), medium)
    s_ = math.copysign(abs(short) ** (1 / 3), short)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _delta_e(left: str, right: str) -> float:
    """两色的 OKLab ΔE ×100。"""
    l1, a1, b1 = _oklab(left)
    l2, a2, b2 = _oklab(right)
    return math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2) * 100.0


def test_all_policies_have_a_color_and_a_marker() -> None:
    """四个策略都要有颜色和标记形状（后者是颜色之外的第二层通道）。"""
    assert set(POLICY_COLORS) == {"naive", "retry_only", "rule_noisy", "rule_oracle"}
    assert set(POLICY_MARKERS) == set(POLICY_COLORS)
    assert len(set(POLICY_COLORS.values())) == len(POLICY_COLORS)
    assert len(set(POLICY_MARKERS.values())) == len(POLICY_MARKERS)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("naive", "retry_only"),
        ("naive", "rule_noisy"),
        ("naive", "rule_oracle"),
        ("retry_only", "rule_noisy"),
        ("retry_only", "rule_oracle"),
        ("rule_noisy", "rule_oracle"),
    ],
)
def test_any_two_policy_colors_are_distinguishable(left: str, right: str) -> None:
    """任意两个策略的颜色都必须达到常视觉可辨门槛。"""
    delta = _delta_e(POLICY_COLORS[left], POLICY_COLORS[right])
    assert delta >= MIN_DELTA_E, (
        f"{left}({POLICY_COLORS[left]}) 与 {right}({POLICY_COLORS[right]}) "
        f"的 ΔE 只有 {delta:.1f}，低于门槛 {MIN_DELTA_E}"
    )


def test_overlapping_rule_lines_are_well_separated() -> None:
    """两条 rule 线是图上唯一会重叠的一对，必须分得更开。"""
    delta = _delta_e(POLICY_COLORS["rule_noisy"], POLICY_COLORS["rule_oracle"])
    assert delta >= 25.0, f"两条 rule 线的 ΔE 只有 {delta:.1f}，重叠时认不出来"
