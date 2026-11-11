"""命令行入口。

``python -m shelf_agent.eval.cli <子命令>`` 或安装后的 ``shelf-agent <子命令>``。

子命令：``list-scenarios`` / ``demo`` / ``sweep`` / ``run``。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from ..config import DEFAULT_CONFIG_PATH, load_config
from .runner import run_episode
from .scenarios import SCENARIOS, get_scenario, scenario_names
from .sweep import DEFAULT_POLICIES, DEFAULT_RATES, DEFAULT_SEEDS, run_sweep

#: ANSI 颜色（``--no-color`` 时全部置空）。
COLORS = {
    "done": "\033[32m",
    "ok": "\033[32m",
    "timeout": "\033[31m",
    "error": "\033[33m",
    "reset": "\033[0m",
}


def _configure_stdio() -> None:
    """把 stdout/stderr 切成 UTF-8（Windows 控制台默认可能是 GBK）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 少数环境不支持
            pass


def _paint(text: str, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="shelf-agent",
        description="螺丝装箱 → 搬运上架的仿真环境与自愈 agent",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-scenarios", help="列出所有命名场景")
    p_list.set_defaults(func=_cmd_list_scenarios)

    p_demo = sub.add_parser("demo", help="跑一个场景并打印时间线")
    p_demo.add_argument("--scenario", default="happy", help="场景名（默认 happy）")
    p_demo.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    p_demo.add_argument("--seed", type=int, default=0, help="随机种子（默认 0）")
    p_demo.add_argument("--show-truth", action="store_true", help="并排显示真值状态")
    p_demo.add_argument("--no-color", action="store_true", help="不要 ANSI 颜色")
    p_demo.set_defaults(func=_cmd_demo)

    p_sweep = sub.add_parser("sweep", help="故障率扫描")
    p_sweep.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    p_sweep.add_argument("--seeds", type=int, default=DEFAULT_SEEDS, help="每个组合的 seed 数")
    p_sweep.add_argument(
        "--failure-rates",
        default=",".join(str(r) for r in DEFAULT_RATES),
        help="故障率列表，逗号分隔",
    )
    p_sweep.add_argument(
        "--policies", default=",".join(DEFAULT_POLICIES), help="策略列表，逗号分隔"
    )
    p_sweep.add_argument("--out", default="results", help="输出目录")
    p_sweep.set_defaults(func=_cmd_sweep)

    p_run = sub.add_parser("run", help="连真实 LLM 跑（需要网络与 API key）")
    p_run.add_argument("--policy", default="react", help="策略名（目前支持 react）")
    p_run.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    p_run.add_argument("--seed", type=int, default=0, help="随机种子")
    p_run.add_argument("--out", default="results/react", help="轨迹输出目录")
    p_run.set_defaults(func=_cmd_run)

    return parser


# ------------------------------------------------------------------- 子命令


def _cmd_list_scenarios(args: argparse.Namespace) -> int:
    print("命名场景：")
    for name in scenario_names():
        scenario = SCENARIOS[name]
        print(f"  {name:28s} {scenario.description}")
        print(f"  {'':28s} 期望结果：{scenario.expected_outcome}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    try:
        scenario = get_scenario(args.scenario)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = run_episode(
        config,
        seed=args.seed,
        policy_name="rule",
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
        episode_id=f"demo-{scenario.name}",
    )
    from .report import render_timeline

    title = f"场景 {scenario.name} —— {scenario.description}"
    print(render_timeline(result.steps, result.outcome, show_truth=args.show_truth, title=title))
    if not args.no_color:
        colored = _paint(result.outcome, "done" if result.outcome == "SUCCESS" else "timeout", True)
        print(f"outcome = {colored}")
    else:
        print(f"outcome = {result.outcome}")
    print(f"makespan_s = {result.metrics['makespan_s']:.1f}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rates = [float(x) for x in str(args.failure_rates).split(",") if x.strip()]
    policies = [x.strip() for x in str(args.policies).split(",") if x.strip()]
    if not rates or not policies:
        print("--failure-rates 与 --policies 不能为空", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    sweep = run_sweep(
        config,
        seeds=args.seeds,
        failure_rates=rates,
        policies=policies,
        out_dir=out_dir,
    )
    print(f"完成：{len(sweep.results)} 个 episode，结果写到 {out_dir}")
    for row in sweep.summary_rows:
        print(
            "  rate={rate:<5} {policy:<12} SUCCESS={succ:<5} FALSE_FINISH={ff:<4} "
            "SAFE_ABORT={sa:<4} unsafe_release={unsafe:<5} makespan={mk:.0f}s".format(
                rate=row["failure_rate"],
                policy=row["policy"],
                succ=row["success"],
                ff=row["false_finish"],
                sa=row["safe_abort"],
                unsafe=row["unsafe_release_rate"],
                mk=row["makespan_mean_s"],
            )
        )
    for name in ("episodes.jsonl", "summary.csv", "summary.md", "success_vs_failure_rate.png"):
        path = out_dir / name
        print(f"  {name:32s} {'存在' if path.is_file() else '缺失'}  {path}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """真实 LLM 模式（Phase 3 实现）。"""
    from ..agents.llm_client import llm_settings_from_env

    try:
        settings = llm_settings_from_env()
    except Exception as exc:  # noqa: BLE001 - CLI 顶层需要把错误说清楚
        print(str(exc), file=sys.stderr)
        return 2
    print(f"使用模型 {settings.model} @ {settings.base_url}（key 已脱敏）")
    return _run_react(args, settings)


def _run_react(args: argparse.Namespace, settings: Any) -> int:
    from ..agents.react_agent import ReActAgent
    from ..utils.jsonl import write_jsonl
    from .runner import run_react_episode

    config = load_config(args.config)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    agent = ReActAgent.from_settings(settings)
    result = run_react_episode(config, seed=args.seed, agent=agent, episode_id=f"react-seed{args.seed}")
    write_jsonl(out_dir / "episodes.jsonl", [result.to_dict()])
    print(f"outcome = {result.outcome}")
    print(f"makespan_s = {result.metrics['makespan_s']:.1f}")
    for name in ("episodes.jsonl",):
        print(f"  {name:32s} {out_dir / name}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。返回进程退出码。"""
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:  # pragma: no cover - 交互中断
        print("已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
