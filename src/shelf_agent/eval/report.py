"""出图与时间线渲染。

图用 matplotlib 的 Agg 后端（无显示环境也能出图）；时间线是纯文本，方便
在组会上直接贴。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (必须在 use("Agg") 之后)

#: 每个策略一条线的颜色。
POLICY_COLORS = {
    "naive": "#c2410c",
    "rule_oracle": "#1d4ed8",
    "rule_noisy": "#0f766e",
}

#: 图上用的中文字体候选（按可用性挑选）。
CJK_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "PingFang SC",
    "Hiragino Sans GB",
    "WenQuanYi Micro Hei",
)


def _setup_fonts() -> None:
    """挑一个可用的中文字体，避免图上出现方块。"""
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    chosen = [name for name in CJK_FONT_CANDIDATES if name in available]
    if chosen:
        plt.rcParams["font.sans-serif"] = chosen + list(plt.rcParams["font.sans-serif"])
    plt.rcParams["axes.unicode_minus"] = False


def _rows_by_policy(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["policy"]), []).append(row)
    for items in grouped.values():
        items.sort(key=lambda r: float(r["failure_rate"]))
    return grouped


def plot_success_vs_failure_rate(
    rows: Sequence[Mapping[str, Any]],
    path: str | Path,
) -> Path:
    """画双图：成功率（带置信区间）与误放行 / 不安全放行比例。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _setup_fonts()
    grouped = _rows_by_policy(rows)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))

    for policy, items in sorted(grouped.items()):
        xs = [float(r["failure_rate"]) for r in items]
        ys = [float(r["success_rate"]) for r in items]
        los = [float(r["success_rate"]) - float(r["success_ci_low"]) for r in items]
        his = [float(r["success_ci_high"]) - float(r["success_rate"]) for r in items]
        color = POLICY_COLORS.get(policy)
        ax1.errorbar(xs, ys, yerr=[los, his], marker="o", capsize=3, label=policy, color=color)
        ax2.plot(
            xs,
            [float(r["false_finish_rate"]) for r in items],
            marker="s",
            linestyle="--",
            label=f"{policy} FALSE_FINISH",
            color=color,
        )
        ax2.plot(
            xs,
            [float(r["unsafe_release_rate"]) for r in items],
            marker="^",
            linestyle=":",
            label=f"{policy} unsafe_release",
            color=color,
        )

    ax1.set_title("成功率随故障注入率变化（含 Wilson 95% CI）")
    ax1.set_xlabel("故障注入率 rate")
    ax1.set_ylabel("SUCCESS 比例")
    ax1.set_ylim(-0.05, 1.05)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)

    ax2.set_title("误放行与不安全放行比例")
    ax2.set_xlabel("故障注入率 rate")
    ax2.set_ylabel("比例")
    ax2.set_ylim(-0.05, 1.05)
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=7)

    fig.suptitle("螺丝装箱 → 搬运上架：策略对比")
    fig.tight_layout()
    fig.savefig(target, dpi=140)
    plt.close(fig)
    return target


def _format_data(data: Mapping[str, Any]) -> str:
    if not data:
        return ""
    parts = []
    for key in sorted(data):
        value = data[key]
        if isinstance(value, bool):
            parts.append(f"{key}={value}")
        elif isinstance(value, float):
            parts.append(f"{key}={value:.3f}")
        else:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def _format_truth(snapshot: Mapping[str, Any]) -> str:
    boxes = snapshot.get("boxes") or {}
    parts = []
    for box_id, box in sorted(boxes.items()):
        parts.append(
            f"{box_id}: 箱内{box.get('inside')} 箱外{box.get('outside')} 位置{box.get('location')}"
        )
    robots = snapshot.get("robots") or {}
    robot_part = " ".join(f"{name}={state}" for name, state in sorted(robots.items()))
    if robot_part:
        parts.append(f"机器人 {robot_part}")
    return " | ".join(parts)


def render_timeline(
    steps: Sequence[Mapping[str, Any]],
    outcome: str,
    show_truth: bool = False,
    title: str = "",
) -> str:
    """把轨迹渲染成可读的时间线。"""
    lines: list[str] = []
    if title:
        lines.append(title)
        lines.append("=" * max(12, len(title)))
    if not steps:
        lines.append("（没有任何调用）")
    for step in steps:
        call = step["call"]
        result = step["result"]
        args = ", ".join(f"{k}={v!r}" for k, v in sorted((call.get("args") or {}).items()))
        head = f"[{float(step['t_s']):7.1f}s] → {call['name']}({args})"
        lines.append(head)
        detail = f"{' ' * 12}← {result['status']}"
        if result.get("error_code"):
            detail += f" [{result['error_code']}]"
        detail += f" ({float(result['duration_s']):.1f}s)"
        data = _format_data(result.get("data") or {})
        if data:
            detail += f" {data}"
        if result.get("message"):
            detail += f"  # {result['message']}"
        lines.append(detail)
        if show_truth:
            truth = _format_truth(step.get("truth_snapshot") or {})
            if truth:
                lines.append(f"{' ' * 12}[真值] {truth}")
    lines.append("")
    lines.append(f"最终结论：{outcome}")
    return "\n".join(lines)
