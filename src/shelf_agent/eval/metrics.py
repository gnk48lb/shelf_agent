"""指标聚合：outcome 分类、比例的 Wilson 区间、均值的标准误、配对比较。"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from ..utils.stats import mean_and_stderr, wilson_interval
from .runner import EpisodeResult

#: 全部 outcome 取值（§5）。
OUTCOMES = (
    "SUCCESS",
    "FALSE_FINISH",
    "SAFE_ABORT",
    "MISSED_SUCCESS",
    "ABORTED_BUDGET",
    "MAX_STEPS",
    "AGENT_ERROR",
)

#: 数值型指标（聚合时给均值与标准误）。
NUMERIC_METRICS = (
    "makespan_s",
    "n_steps",
    "n_timeouts",
    "n_resets",
    "n_unnecessary_resets",
    "n_wasted_calls",
    "n_human_asks",
    "n_invalid_calls",
    "unsafe_release",
)


def outcome_counts(results: Iterable[EpisodeResult]) -> dict[str, int]:
    """各 outcome 的计数（缺省为 0，保证键齐全）。"""
    counts = Counter(r.outcome for r in results)
    return {name: counts.get(name, 0) for name in OUTCOMES}


def outcome_rates(results: Sequence[EpisodeResult]) -> dict[str, dict[str, float]]:
    """各 outcome 的比例与 Wilson 95% 置信区间。"""
    counts = outcome_counts(results)
    total = len(results)
    out: dict[str, dict[str, float]] = {}
    for name in OUTCOMES:
        lo, hi = wilson_interval(counts[name], total)
        out[name] = {
            "count": counts[name],
            "rate": counts[name] / total if total else 0.0,
            "ci_low": lo,
            "ci_high": hi,
            "n": total,
        }
    return out


def metric_summary(results: Sequence[EpisodeResult], key: str) -> dict[str, float]:
    """数值指标的均值、标准误与样本量。"""
    values = [float(r.metrics.get(key, 0.0)) for r in results]
    mean, stderr = mean_and_stderr(values)
    return {"mean": mean, "stderr": stderr, "n": float(len(values))}


def aggregate(results: Sequence[EpisodeResult]) -> dict[str, Any]:
    """把一批 episode 汇总成一张表。"""
    summary: dict[str, Any] = {
        "n_episodes": len(results),
        "outcomes": outcome_counts(results),
        "rates": outcome_rates(results),
        "metrics": {key: metric_summary(results, key) for key in NUMERIC_METRICS},
    }
    unsafe = sum(1 for r in results if r.metrics.get("unsafe_release"))
    lo, hi = wilson_interval(unsafe, len(results))
    summary["unsafe_release_rate"] = {
        "count": unsafe,
        "rate": unsafe / len(results) if results else 0.0,
        "ci_low": lo,
        "ci_high": hi,
    }
    return summary


def group_by_seed(results: Sequence[EpisodeResult]) -> dict[int, EpisodeResult]:
    """按 seed 归组（同一 seed 只保留最后一条）。"""
    return {r.seed: r for r in results}


def _group_key(result: EpisodeResult) -> tuple[float, int]:
    """配对键：故障率 + seed。

    同一批 seed 会跑多个故障率，只按 seed 建字典会把前面的结果覆盖掉，
    所以配对必须带上故障率。
    """
    return (float(result.metrics.get("failure_rate", 0.0)), result.seed)


def group_by_rate_seed(results: Sequence[EpisodeResult]) -> dict[tuple[float, int], EpisodeResult]:
    """按 (故障率, seed) 归组——配对比较的正确粒度。"""
    return {_group_key(r): r for r in results}


def paired_compare(
    left: Sequence[EpisodeResult],
    right: Sequence[EpisodeResult],
    left_name: str = "left",
    right_name: str = "right",
    rate: float | None = None,
) -> dict[str, Any]:
    """按相同 (故障率, seed) 配对比较两个策略的成功率。

    ``rate`` 不为 None 时只比较该故障率下的结果。返回配对后的胜负平、成功率差。
    """
    left_map = group_by_rate_seed(left)
    right_map = group_by_rate_seed(right)
    if rate is not None:
        left_map = {k: v for k, v in left_map.items() if k[0] == rate}
        right_map = {k: v for k, v in right_map.items() if k[0] == rate}
    shared = sorted(set(left_map) & set(right_map))
    wins = losses = ties = 0
    diffs: list[float] = []
    for key in shared:
        a = left_map[key].outcome == "SUCCESS"
        b = right_map[key].outcome == "SUCCESS"
        diffs.append(float(a) - float(b))
        if a and not b:
            wins += 1
        elif b and not a:
            losses += 1
        else:
            ties += 1
    n = len(shared)
    lo, hi = wilson_interval(wins + losses, n) if n else (0.0, 0.0)
    mean_diff = sum(diffs) / n if n else 0.0
    return {
        "left": left_name,
        "right": right_name,
        "failure_rate": rate,
        "paired_seeds": n,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "success_rate_left": (
            sum(1.0 for k in shared if left_map[k].outcome == "SUCCESS") / n if n else 0.0
        ),
        "success_rate_right": (
            sum(1.0 for k in shared if right_map[k].outcome == "SUCCESS") / n if n else 0.0
        ),
        "mean_diff": mean_diff,
        "ci_low": lo,
        "ci_high": hi,
    }


def format_summary_markdown(
    summary_by_key: Mapping[str, Mapping[str, Any]],
    title: str = "评测汇总",
) -> str:
    """把聚合结果渲染成 markdown 表格。"""
    lines = [f"# {title}", ""]
    lines.append("| 组 | episodes | SUCCESS | FALSE_FINISH | SAFE_ABORT | 其他 | unsafe_release | makespan_s |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for key, summary in summary_by_key.items():
        rates = summary["rates"]
        others = sum(
            rates[name]["count"]
            for name in ("MISSED_SUCCESS", "ABORTED_BUDGET", "MAX_STEPS", "AGENT_ERROR")
        )
        makespan = summary["metrics"]["makespan_s"]
        lines.append(
            "| {key} | {n} | {succ} | {ff} | {sa} | {other} | {unsafe} | {makespan:.1f} |".format(
                key=key,
                n=summary["n_episodes"],
                succ=rates["SUCCESS"]["count"],
                ff=rates["FALSE_FINISH"]["count"],
                sa=rates["SAFE_ABORT"]["count"],
                other=others,
                unsafe=summary["unsafe_release_rate"]["count"],
                makespan=makespan["mean"],
            )
        )
    lines.append("")
    return "\n".join(lines)
