"""故障率扫描与验证器质量扫描：多策略 × 多档位 × 多 seed。

产出（写到 ``--out`` 目录）：

* ``episodes.jsonl`` —— 每个 episode 一行（含完整轨迹与真值轨迹）；
* ``summary.csv`` / ``summary.md`` —— 每个 (档位, 策略) 一行的汇总；
* ``success_vs_failure_rate.png`` —— 双图（成功率 / 误放行与不安全放行）。

验证器质量扫描（``sweep-verifier``）复用同一套 :func:`run_sweep`，只把
``verifier.boundary_confusion`` 与 ``verifier.p_outside_miss`` 一起抬到同一档位。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..config import AppConfig, build_config
from ..utils.jsonl import write_jsonl
from ..utils.stats import wilson_interval
from .metrics import OUTCOMES, aggregate
from .runner import EpisodeResult, run_episode

#: 默认的扫描参数。
DEFAULT_SEEDS = 100
DEFAULT_RATES = (0.0, 0.05, 0.1, 0.2, 0.3)
DEFAULT_POLICIES = ("naive", "retry_only", "rule_oracle", "rule_noisy")

#: 验证器质量扫描的默认档位。
DEFAULT_LEVELS = (0.0, 0.1, 0.25, 0.5, 1.0)

#: summary.csv 的固定列（x 轴列另行插入）。
SUMMARY_COLUMNS = (
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
    "false_finish_ci_low",
    "false_finish_ci_high",
    "unsafe_release_rate",
    "unsafe_release_ci_low",
    "unsafe_release_ci_high",
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
    x_key: str = "failure_rate"

    @property
    def grouped(self) -> dict[tuple[float, str], list[EpisodeResult]]:
        groups: dict[tuple[float, str], list[EpisodeResult]] = {}
        for result in self.results:
            key = (float(result.metrics.get(self.x_key, 0.0)), result.policy)
            groups.setdefault(key, []).append(result)
        return groups


def run_sweep(
    config: AppConfig,
    seeds: int = DEFAULT_SEEDS,
    failure_rates: Sequence[float] = DEFAULT_RATES,
    policies: Sequence[str] = DEFAULT_POLICIES,
    out_dir: str | Path = "results",
    write_files: bool = True,
    x_key: str = "failure_rate",
    metrics_extra: Mapping[str, Any] | None = None,
) -> SweepResult:
    """跑一次扫描。``metrics_extra`` 会盖到每个 episode 的 metrics 上（如档位）。"""
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
                if metrics_extra:
                    result.metrics.update(metrics_extra)
                results.append(result)
    rows = build_summary_rows(results, x_key=x_key)
    digest = config.config_hash(
        {"failure_rates": list(failure_rates), "policies": list(policies), "seeds": seeds}
    )
    if write_files:
        write_sweep_files(
            out,
            results,
            rows,
            config,
            seeds,
            failure_rates,
            policies,
            x_key=x_key,
        )
    return SweepResult(
        results=results, summary_rows=rows, out_dir=out, config_hash=digest, x_key=x_key
    )


def build_summary_rows(
    results: Sequence[EpisodeResult],
    x_key: str = "failure_rate",
) -> list[dict[str, Any]]:
    """按 (档位, 策略) 汇总成行。"""
    groups: dict[tuple[float, str], list[EpisodeResult]] = {}
    for result in results:
        key = (float(result.metrics.get(x_key, 0.0)), result.policy)
        groups.setdefault(key, []).append(result)
    rows: list[dict[str, Any]] = []
    for (level, policy), items in sorted(groups.items()):
        summary = aggregate(items)
        rates = summary["rates"]
        metrics = summary["metrics"]
        row: dict[str, Any] = {
            x_key: level,
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
        row["false_finish_ci_low"], row["false_finish_ci_high"] = wilson_interval(
            rates["FALSE_FINISH"]["count"], summary["n_episodes"]
        )
        row["unsafe_release_ci_low"], row["unsafe_release_ci_high"] = wilson_interval(
            summary["unsafe_release_rate"]["count"], summary["n_episodes"]
        )
        row["makespan_mean_s"] = metrics["makespan_s"]["mean"]
        row["makespan_stderr_s"] = metrics["makespan_s"]["stderr"]
        row["mean_n_human_asks"] = metrics["n_human_asks"]["mean"]
        row["mean_n_resets"] = metrics["n_resets"]["mean"]
        rows.append(row)
    return rows


def run_verifier_sweep(
    config: AppConfig,
    failure_rate: float = 0.3,
    levels: Sequence[float] = DEFAULT_LEVELS,
    seeds: int = 200,
    policies: Sequence[str] = ("rule_oracle", "rule_noisy", "retry_only"),
    out_dir: str | Path = "results/verifier",
    write_files: bool = True,
) -> SweepResult:
    """验证器质量扫描：把噪声档位当自变量。

    每个档位把 ``verifier.boundary_confusion`` 与 ``verifier.p_outside_miss``
    同时设成该档位（``p_inside_correct`` / ``p_unclear`` 保持配置值），
    底层复用 :func:`run_sweep`。
    """
    out = Path(out_dir)
    results: list[EpisodeResult] = []
    for level in levels:
        level_config = _config_at_level(config, float(level))
        sweep = run_sweep(
            level_config,
            seeds=seeds,
            failure_rates=[float(failure_rate)],
            policies=policies,
            out_dir=out,
            write_files=False,
            x_key="verifier_level",
            metrics_extra={"verifier_level": float(level)},
        )
        for result in sweep.results:
            result.config_hash = level_config.config_hash(
                {
                    "verifier_level": float(level),
                    "failure_rate": float(failure_rate),
                    "policies": list(policies),
                    "seeds": seeds,
                }
            )
        results.extend(sweep.results)
    rows = build_summary_rows(results, x_key="verifier_level")
    digest = config.config_hash(
        {
            "verifier_levels": list(levels),
            "failure_rate": float(failure_rate),
            "policies": list(policies),
            "seeds": seeds,
        }
    )
    if write_files:
        write_verifier_sweep_files(
            out,
            results,
            rows,
            config,
            seeds,
            levels,
            policies,
            failure_rate=failure_rate,
        )
    return SweepResult(
        results=results,
        summary_rows=rows,
        out_dir=out,
        config_hash=digest,
        x_key="verifier_level",
    )


def _config_at_level(config: AppConfig, level: float) -> AppConfig:
    """造一个「噪声档位 = level」的配置副本。"""
    raw = {
        **config.raw,
        "verifier": {
            **config.raw["verifier"],
            "boundary_confusion": level,
            "p_outside_miss": level,
        },
    }
    return build_config(raw, source_path=config.source_path)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], x_key: str) -> None:
    columns = (x_key, "policy") + SUMMARY_COLUMNS
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def write_sweep_files(
    out_dir: Path,
    results: Sequence[EpisodeResult],
    rows: Sequence[Mapping[str, Any]],
    config: AppConfig,
    seeds: int,
    failure_rates: Sequence[float],
    policies: Sequence[str],
    x_key: str = "failure_rate",
) -> None:
    """把结果写到磁盘。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "episodes.jsonl", [r.to_dict() for r in results])
    _write_csv(out_dir / "summary.csv", rows, x_key)
    from .report import render_sweep_markdown

    markdown = render_sweep_markdown(
        rows, results, seeds, failure_rates, policies, config, x_key
    )
    (out_dir / "summary.md").write_text(markdown, encoding="utf-8")
    from .report import plot_success_vs_failure_rate

    plot_success_vs_failure_rate(rows, out_dir / "success_vs_failure_rate.png")


def write_verifier_sweep_files(
    out_dir: Path,
    results: Sequence[EpisodeResult],
    rows: Sequence[Mapping[str, Any]],
    config: AppConfig,
    seeds: int,
    levels: Sequence[float],
    policies: Sequence[str],
    failure_rate: float = 0.3,
) -> None:
    """验证器质量扫描的落盘。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "episodes.jsonl", [r.to_dict() for r in results])
    _write_csv(out_dir / "verifier_sweep.csv", rows, "verifier_level")
    from .report import render_verifier_markdown

    markdown = render_verifier_markdown(rows, seeds, levels, policies, failure_rate, config)
    (out_dir / "verifier_sweep.md").write_text(markdown, encoding="utf-8")
    from .report import plot_false_finish_vs_level

    plot_false_finish_vs_level(rows, out_dir / "verifier_sweep.png")
