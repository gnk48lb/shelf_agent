"""扫描结果的 markdown 渲染（summary.md / verifier_sweep.md 的正文）。

从 ``eval/report.py`` 拆出来：那个文件同时管出图与时间线渲染，已经接近
400 行的上限（§0.6）。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .metrics import paired_compare


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
        "|---|---|---|---|---|---|---|---|",
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
