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

from .metrics import paired_compare

#: 每个策略一条线的颜色。
POLICY_COLORS = {
    "naive": "#c2410c",
    "retry_only": "#b45309",
    "rule_oracle": "#1d4ed8",
    "rule_noisy": "#0f766e",
}

#: x 轴上的固定微偏移量级（避免同一条 x 上多条线的误差棒互相遮挡）。
DODGE_STEP = 0.004

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
        items.sort(key=lambda r: float(r[_x_key(rows)]))
    return grouped


def _x_key(rows: Sequence[Mapping[str, Any]]) -> str:
    """这张图的 x 轴字段（故障率或验证器档位）。"""
    return "verifier_level" if rows and "verifier_level" in rows[0] else "failure_rate"


def _dodge(policies: Sequence[str]) -> dict[str, float]:
    """给每个策略一个固定的微小 x 偏移。"""
    names = sorted(policies)
    if len(names) <= 1:
        return {name: 0.0 for name in names}
    center = (len(names) - 1) / 2
    return {name: (index - center) * DODGE_STEP for index, name in enumerate(names)}


def _error_bars(
    rows: Sequence[Mapping[str, Any]],
    rate_key: str,
    ci_prefix: str,
) -> tuple[list[float], list[float]]:
    """点估计到 Wilson 区间上下界的距离（**非负**）。

    Wilson 区间在小样本 + 满分时**不包含点估计**（比如 10/10 的上界只有 0.875），
    直接相减会得到负的误差棒长度，matplotlib 会报错。所以这里夹到 0。
    """
    los: list[float] = []
    his: list[float] = []
    for row in rows:
        rate = float(row[rate_key])
        lo = float(row.get(f"{ci_prefix}_ci_low", rate))
        hi = float(row.get(f"{ci_prefix}_ci_high", rate))
        los.append(max(0.0, rate - lo))
        his.append(max(0.0, hi - rate))
    return los, his


def plot_success_vs_failure_rate(
    rows: Sequence[Mapping[str, Any]],
    path: str | Path,
) -> Path:
    """画双图：成功率（带置信区间）与误放行 / 不安全放行比例。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _setup_fonts()
    grouped = _rows_by_policy(rows)
    offsets = _dodge(list(grouped))
    x_key = _x_key(rows)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))

    for policy, items in sorted(grouped.items()):
        xs = [float(r[x_key]) + offsets[policy] for r in items]
        ys = [float(r["success_rate"]) for r in items]
        los, his = _error_bars(items, "success_rate", "success")
        color = POLICY_COLORS.get(policy)
        ax1.errorbar(xs, ys, yerr=[los, his], marker="o", capsize=3, label=policy, color=color)
        ax2.plot(
            xs,
            [float(r["false_finish_rate"]) for r in items],
            marker="s",
            linestyle="-",
            label=f"{policy} FALSE_FINISH",
            color=color,
        )
        ax2.plot(
            xs,
            [float(r["unsafe_release_rate"]) for r in items],
            marker="^",
            linestyle="--",
            label=f"{policy} unsafe_release",
            color=color,
        )

    ax1.set_title("成功率随" + ("验证器噪声档位" if x_key == "verifier_level" else "故障注入率") + "变化（含 Wilson 95% CI）")
    ax1.set_xlabel(x_key)
    ax1.set_ylabel("SUCCESS 比例")
    ax1.set_ylim(-0.05, 1.05)
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)

    ax2.set_title("误放行与不安全放行比例（实线 FALSE_FINISH / 虚线 unsafe_release）")
    ax2.set_xlabel(x_key)
    ax2.set_ylabel("比例")
    ax2.set_ylim(-0.05, 1.05)
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=7)

    fig.suptitle("螺丝装箱 → 搬运上架：策略对比")
    fig.tight_layout()
    fig.savefig(target, dpi=140)
    plt.close(fig)
    return target


def plot_false_finish_vs_level(
    rows: Sequence[Mapping[str, Any]],
    path: str | Path,
) -> Path:
    """验证器质量扫描：FALSE_FINISH 率与 unsafe_release 率 vs 噪声档位。

    ``retry_only`` 画成参考线（它不验证，档位对它没有影响）。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _setup_fonts()
    grouped = _rows_by_policy(rows)
    offsets = _dodge(list(grouped))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))
    for policy, items in sorted(grouped.items()):
        xs = [float(r["verifier_level"]) + offsets[policy] for r in items]
        color = POLICY_COLORS.get(policy)
        style = "-" if policy != "retry_only" else ":"
        for axis, key, marker in (
            (ax1, "false_finish_rate", "s"),
            (ax2, "unsafe_release_rate", "^"),
        ):
            ys = [float(r[key]) for r in items]
            los, his = _error_bars(items, key, key.replace("_rate", ""))
            axis.errorbar(
                xs,
                ys,
                yerr=[los, his],
                marker=marker,
                linestyle=style,
                capsize=3,
                label=policy,
                color=color,
            )
    ax1.set_title("误放行率随验证器噪声档位变化")
    ax1.set_ylabel("FALSE_FINISH 比例")
    ax2.set_title("不安全放行率随验证器噪声档位变化")
    ax2.set_ylabel("unsafe_release 比例")
    for axis in (ax1, ax2):
        axis.set_xlabel("验证器噪声档位 level")
        axis.set_ylim(-0.02, 1.0)
        axis.grid(alpha=0.3)
        axis.legend(fontsize=8)
    fig.suptitle("验证器质量扫描（level 同时抬高 boundary_confusion 与 p_outside_miss）")
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


def _format_event(event: Mapping[str, Any] | None) -> str:
    """把真值通道里的「本次注入事件」渲染成一行标注。"""
    if not event:
        return ""
    kind = event.get("type")
    if kind == "injected":
        params = event.get("params") or {}
        detail = ""
        if params:
            detail = " " + ", ".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"[注入] {event.get('kind')}{detail}"
    if kind == "precondition":
        return f"[前置违规] {event.get('reason')}"
    return f"[事件] {event.get('kind')}"


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
    event = _format_event(snapshot.get("last_event"))
    if event:
        parts.append(event)
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
        message = result.get("message") or ""
        # message 与 status 相同时不重复打印（文案只取决于 status，见 §A1）
        if message and message != result["status"]:
            detail += f"  # {message}"
        lines.append(detail)
        if show_truth:
            truth = _format_truth(step.get("truth_snapshot") or {})
            if truth:
                lines.append(f"{' ' * 12}[真值] {truth}")
    lines.append("")
    lines.append(f"最终结论：{outcome}")
    return "\n".join(lines)


def render_sweep_markdown(
    rows: Sequence[Mapping[str, Any]],
    results: Sequence[Any],
    seeds: int,
    failure_rates: Sequence[float],
    policies: Sequence[str],
    config: Any,
    x_key: str = "failure_rate",
) -> str:
    """故障率扫描的 summary.md 正文。"""
    lines = [
        "# 故障率扫描结果",
        "",
        f"- 配置：`{config.source_path}`（scenario={config.scenario}，N={config.target_screws}）",
        f"- seeds：{seeds}（每个故障率 × 每个策略共用同一批 seed，配对比较）",
        f"- 故障率：{', '.join(str(r) for r in failure_rates)}",
        f"- 策略：{', '.join(policies)}",
        f"- 目标螺丝数 N = {config.target_screws}；失败率参数见 `configs/real_screw.yaml`",
        "",
        "| failure_rate | policy | episodes | SUCCESS | FALSE_FINISH | SAFE_ABORT | 其他 | "
        "SUCCESS 95% CI | unsafe_release | makespan_s (mean±se) | 求助次数 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        others = (
            int(row["missed_success"])
            + int(row["aborted_budget"])
            + int(row["max_steps"])
            + int(row["agent_error"])
        )
        lines.append(
            "| {rate} | {policy} | {n} | {succ} | {ff} | {sa} | {other} | "
            "[{lo:.3f}, {hi:.3f}] | {unsafe} | {mk:.1f}±{se:.1f} | {human:.2f} |".format(
                rate=row[x_key],
                policy=row["policy"],
                n=row["n_episodes"],
                succ=row["success"],
                ff=row["false_finish"],
                sa=row["safe_abort"],
                other=others,
                lo=row["success_ci_low"],
                hi=row["success_ci_high"],
                unsafe=row["unsafe_release_rate"],
                mk=row["makespan_mean_s"],
                se=row["makespan_stderr_s"],
                human=row["mean_n_human_asks"],
            )
        )
    lines.append("")
    lines.append("## 配对比较（同一批 seed）")
    lines.append("")
    base = policies[-1] if policies else ""
    for policy in policies[:-1]:
        left = [r for r in results if r.policy == policy]
        right = [r for r in results if r.policy == base]
        if not left or not right:
            continue
        comparison = paired_compare(left, right, policy, base)
        lines.append(
            f"- {policy} vs {base}：配对 {comparison['paired_seeds']} 个 seed，"
            f"胜 {comparison['wins']} / 负 {comparison['losses']} / 平 {comparison['ties']}；"
            f"成功率 {comparison['success_rate_left']:.3f} vs {comparison['success_rate_right']:.3f}"
        )
    lines.append("")
    return "\n".join(lines)


def render_verifier_markdown(
    rows: Sequence[Mapping[str, Any]],
    seeds: int,
    levels: Sequence[float],
    policies: Sequence[str],
    failure_rate: float,
    config: Any,
) -> str:
    """验证器质量扫描的 verifier_sweep.md 正文。"""
    lines = [
        "# 验证器质量扫描",
        "",
        f"- 配置：`{config.source_path}`（N={config.target_screws}）",
        f"- 故障注入率固定为 {failure_rate}；seeds = {seeds}（每个档位 × 每个策略共用同一批 seed）",
        f"- 档位：{', '.join(str(level) for level in levels)}"
        "（每个档位同时抬高 `verifier.boundary_confusion` 与 `verifier.p_outside_miss`；"
        "`p_inside_correct` 与 `p_unclear` 保持配置值）",
        f"- 策略：{', '.join(policies)}",
        "",
        "> level=0 **不等于无噪声**：`p_inside_correct=0.95` 仍会产生 ±1 的计数噪声"
        "（真值 N−1 时仍有一定概率被报成 N）。",
        "",
        "| level | policy | episodes | SUCCESS | FALSE_FINISH | 误放行率 95% CI | "
        "unsafe_release | 误放行率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {level} | {policy} | {n} | {succ} | {ff} | [{lo:.4f}, {hi:.4f}] | {unsafe} | {rate:.4f} |".format(
                level=row["verifier_level"],
                policy=row["policy"],
                n=row["n_episodes"],
                succ=row["success"],
                ff=row["false_finish"],
                lo=row["false_finish_ci_low"],
                hi=row["false_finish_ci_high"],
                unsafe=row["unsafe_release_rate"],
                rate=row["false_finish_rate"],
            )
        )
    lines.append("")
    return "\n".join(lines)
