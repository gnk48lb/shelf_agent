"""把**真实运行**的结果渲染进 README 的 §7（三个占位标记）。

用法（跑完 sweep 之后）::

    uv run python -m shelf_agent.eval.cli sweep --seeds 100 --failure-rates 0,0.05,0.1,0.2,0.3 \
        --policies naive,retry_only,rule_oracle,rule_noisy --out results/
    uv run python -m shelf_agent.eval.cli sweep-verifier --failure-rate 0.3 \
        --levels 0,0.1,0.25,0.5,1.0 --seeds 200 \
        --policies rule_oracle,rule_noisy,retry_only --out results/verifier/
    uv run python scripts/render_readme_tables.py --out results --readme README.md

这样 README 里的每个数字都直接来自那两次运行的产物，不需要手抄。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

#: README 里的占位标记。
MARK_SWEEP = "<!-- SWEEP_TABLE -->"
MARK_PAIRED = "<!-- PAIRED_TABLE -->"
MARK_VERIFIER = "<!-- VERIFIER_TABLE -->"


def _load_episodes(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _sweep_table(rows: Iterable[dict[str, str]]) -> str:
    lines = [
        "| failure_rate | policy | SUCCESS | FALSE_FINISH | SAFE_ABORT | SUCCESS 95% CI | "
        "unsafe_release | makespan_s (mean±se) | 求助次数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {rate} | {policy} | {succ} | {ff} | {sa} | [{lo:.3f}, {hi:.3f}] | {unsafe} | "
            "{mk:.1f}±{se:.1f} | {human:.2f} |".format(
                rate=row["failure_rate"],
                policy=row["policy"],
                succ=row["success"],
                ff=row["false_finish"],
                sa=row["safe_abort"],
                lo=float(row["success_ci_low"]),
                hi=float(row["success_ci_high"]),
                unsafe=float(row["unsafe_release_rate"]),
                mk=float(row["makespan_mean_s"]),
                se=float(row["makespan_stderr_s"]),
                human=float(row["mean_n_human_asks"]),
            )
        )
    return "\n".join(lines)


def _paired_table(episodes: list[dict[str, Any]], base: str = "rule_noisy") -> str:
    """逐故障率的配对比较（胜 = 左策略成功而右策略失败）。"""
    by_key: dict[tuple[float, int, str], str] = {}
    rates: set[float] = set()
    policies: set[str] = set()
    for record in episodes:
        metrics = record.get("metrics") or {}
        rate = float(metrics.get("failure_rate", 0.0))
        policy = str(record["policy"])
        rates.add(rate)
        policies.add(policy)
        by_key[(rate, int(record["seed"]), policy)] = str(record["outcome"])
    others = sorted(p for p in policies if p != base)
    header = "| failure_rate | " + " | ".join(f"{p} vs {base}" for p in others) + " |"
    sep = "|---|" + "---|" * len(others)
    lines = [header, sep]
    for rate in sorted(rates):
        cells = []
        for policy in others:
            wins = losses = ties = 0
            seeds = {s for (r, s, p) in by_key if r == rate and p == policy}
            for seed in seeds:
                left = by_key.get((rate, seed, policy)) == "SUCCESS"
                right = by_key.get((rate, seed, base)) == "SUCCESS"
                if left and not right:
                    wins += 1
                elif right and not left:
                    losses += 1
                else:
                    ties += 1
            cells.append(f"胜 {wins} / 负 {losses} / 平 {ties}")
        lines.append(f"| {rate} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _verifier_table(path: Path) -> str:
    lines = [
        "| level | policy | episodes | SUCCESS | FALSE_FINISH | 误放行率 95% CI | "
        "unsafe_release | 误放行率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    with path.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            lines.append(
                "| {level} | {policy} | {n} | {succ} | {ff} | [{lo:.4f}, {hi:.4f}] | {unsafe} | "
                "{rate:.4f} |".format(
                    level=row["verifier_level"],
                    policy=row["policy"],
                    n=row["n_episodes"],
                    succ=row["success"],
                    ff=row["false_finish"],
                    lo=float(row["false_finish_ci_low"]),
                    hi=float(row["false_finish_ci_high"]),
                    unsafe=float(row["unsafe_release_rate"]),
                    rate=float(row["false_finish_rate"]),
                )
            )
    return "\n".join(lines)


def _replace(text: str, marker: str, block: str) -> str:
    if marker not in text:
        raise SystemExit(f"README 里找不到占位标记 {marker}")
    return text.replace(marker, block)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 sweep 结果渲染进 README")
    parser.add_argument("--out", default="results", help="sweep 输出目录")
    parser.add_argument("--readme", default="README.md", help="README 路径")
    args = parser.parse_args(argv)
    out = Path(args.out)
    readme_path = Path(args.readme)

    with (out / "summary.csv").open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    episodes = _load_episodes(out / "episodes.jsonl")
    text = readme_path.read_text(encoding="utf-8")
    text = _replace(text, MARK_SWEEP, _sweep_table(rows))
    text = _replace(text, MARK_PAIRED, _paired_table(episodes))

    verifier_csv = out / "verifier" / "verifier_sweep.csv"
    if verifier_csv.is_file():
        text = _replace(text, MARK_VERIFIER, _verifier_table(verifier_csv))
    else:
        print(f"警告：没有找到 {verifier_csv}，§7.2 的表保持原样", file=sys.stderr)

    readme_path.write_text(text, encoding="utf-8")
    print(f"已更新 {readme_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
