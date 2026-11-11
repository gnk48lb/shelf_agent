"""故障率扫描：多策略 × 多故障率 × 多 seed，产出可汇报的结果文件。

产出（写到 ``--out`` 目录）：

* ``episodes.jsonl`` —— 每个 episode 一行（含完整轨迹与真值轨迹）；
* ``summary.csv`` —— 每个 (故障率, 策略) 一行的汇总；
* ``summary.md`` —— 给人看的表格；
* ``success_vs_failure_rate.png`` —— 双图（成功率 / 误放行与不安全放行）。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..config import AppConfig
from ..utils.jsonl import write_jsonl
from .metrics import OUTCOMES, aggregate, paired_compare
from .runner import EpisodeResult, run_episode

#: 默认的扫描参数。
DEFAULT_SEEDS = 100
DEFAULT_RATES = (0.0, 0.05, 0.1, 0.2, 0.3)
DEFAULT_POLICIES = ("naive", "rule_oracle", "rule_noisy")

#: summary.csv 的列。
SUMMARY_COLUMNS = (
    "failure_rate",
    "policy",
    "n_episodes",
    "success",
    "false_finish",
    "safe_abort",
    "missed_success",
    "aborted_budget",
    "max_steps",
    "agent_error",
    "success_rate",
    "success_ci_low",
    "success_ci_high",
    "false_finish_rate",
    "unsafe_release_rate",
    "makespan_mean_s",
    "makespan_stderr_s",
    "mean_n_human_asks",
    "mean_n_resets",
)


@dataclass
class SweepResult:
    """一次扫描的产物。"""

    results: list[EpisodeResult]
    summary_rows: list[dict[str, Any]]
    out_dir: Path
    config_hash: str

    @property
    def grouped(self) -> dict[tuple[float, str], list[EpisodeResult]]:
        groups: dict[tuple[float, str], list[EpisodeResult]] = {}
        for result in self.results:
            key = (float(result.metrics.get("failure_rate", 0.0)), result.policy)
            groups.setdefault(key, []).append(result)
        return groups


def run_sweep(
    config: AppConfig,
    seeds: int = DEFAULT_SEEDS,
    failure_rates: Sequence[float] = DEFAULT_RATES,
    policies: Sequence[str] = DEFAULT_POLICIES,
    out_dir: str | Path = "results",
    write_files: bool = True,
) -> SweepResult:
    """跑一次扫描。"""
    out = Path(out_dir)
    results: list[EpisodeResult] = []
    for rate in failure_rates:
        for policy_name in policies:
            for seed in range(seeds):
                result = run_episode(
                    config,
                    seed=seed,
                    policy_name=policy_name,
                    failure_rate=float(rate),
                    hash_overrides={
                        "failure_rate": float(rate),
                        "policy": policy_name,
                        "seeds": seeds,
                    },
                    episode_id=f"{policy_name}-r{rate}-seed{seed}",
                )
                result.metrics["failure_rate"] = float(rate)
                results.append(result)
    rows = build_summary_rows(results)
    digest = config.config_hash(
        {"failure_rates": list(failure_rates), "policies": list(policies), "seeds": seeds}
    )
    if write_files:
        write_sweep_files(out, results, rows, config, seeds, failure_rates, policies)
    return SweepResult(results=results, summary_rows=rows, out_dir=out, config_hash=digest)


def build_summary_rows(results: Sequence[EpisodeResult]) -> list[dict[str, Any]]:
    """按 (故障率, 策略) 汇总成行。"""
    groups: dict[tuple[float, str], list[EpisodeResult]] = {}
    for result in results:
        key = (float(result.metrics.get("failure_rate", 0.0)), result.policy)
        groups.setdefault(key, []).append(result)
    rows: list[dict[str, Any]] = []
    for (rate, policy), items in sorted(groups.items()):
        summary = aggregate(items)
        rates = summary["rates"]
        metrics = summary["metrics"]
        row: dict[str, Any] = {
            "failure_rate": rate,
            "policy": policy,
            "n_episodes": summary["n_episodes"],
        }
        for name in OUTCOMES:
            row[name.lower()] = rates[name]["count"]
        row["success_rate"] = rates["SUCCESS"]["rate"]
        row["success_ci_low"] = rates["SUCCESS"]["ci_low"]
        row["success_ci_high"] = rates["SUCCESS"]["ci_high"]
        row["false_finish_rate"] = rates["FALSE_FINISH"]["rate"]
        row["unsafe_release_rate"] = summary["unsafe_release_rate"]["rate"]
        row["makespan_mean_s"] = metrics["makespan_s"]["mean"]
        row["makespan_stderr_s"] = metrics["makespan_s"]["stderr"]
        row["mean_n_human_asks"] = metrics["n_human_asks"]["mean"]
        row["mean_n_resets"] = metrics["n_resets"]["mean"]
        rows.append(row)
    return rows


def write_sweep_files(
    out_dir: Path,
    results: Sequence[EpisodeResult],
    rows: Sequence[Mapping[str, Any]],
    config: AppConfig,
    seeds: int,
    failure_rates: Sequence[float],
    policies: Sequence[str],
) -> None:
    """把结果写到磁盘。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "episodes.jsonl", [r.to_dict() for r in results])
    with (out_dir / "summary.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in SUMMARY_COLUMNS})
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        grouped.setdefault(f"{row['failure_rate']}|{row['policy']}", row)
    markdown = _summary_markdown(rows, results, seeds, failure_rates, policies, config)
    (out_dir / "summary.md").write_text(markdown, encoding="utf-8")
    from .report import plot_success_vs_failure_rate

    plot_success_vs_failure_rate(rows, out_dir / "success_vs_failure_rate.png")


def _summary_markdown(
    rows: Sequence[Mapping[str, Any]],
    results: Sequence[EpisodeResult],
    seeds: int,
    failure_rates: Sequence[float],
    policies: Sequence[str],
    config: AppConfig,
) -> str:
    """summary.md 的正文。"""
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
                rate=row["failure_rate"],
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
    lines.append("## 配对比较（同一批 seed，逐故障率）")
    lines.append("")
    base = policies[-1] if policies else ""
    for rate in failure_rates:
        for policy in policies[:-1]:
            left = [r for r in results if r.policy == policy]
            right = [r for r in results if r.policy == base]
            if not left or not right:
                continue
            comparison = paired_compare(left, right, policy, base, rate=rate)
            lines.append(
                f"- rate={rate} · {policy} vs {base}：配对 {comparison['paired_seeds']} 个 seed，"
                f"胜 {comparison['wins']} / 负 {comparison['losses']} / 平 {comparison['ties']}；"
                f"成功率 {comparison['success_rate_left']:.3f} vs {comparison['success_rate_right']:.3f}"
            )
    lines.append("")
    return "\n".join(lines)
