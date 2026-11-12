"""评测层单测：outcome 分类、指标聚合、配对 seed、端到端 sweep、CLI。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

import pytest

from shelf_agent.config import load_config
from shelf_agent.contracts.types import EpisodeInfo, ToolCall, ToolResult
from shelf_agent.eval.cli import main
from shelf_agent.eval.metrics import (
    OUTCOMES,
    aggregate,
    outcome_counts,
    outcome_rates,
    paired_compare,
)
from shelf_agent.eval.runner import classify_outcome, run_episode
from shelf_agent.eval.scenarios import SCENARIOS
from shelf_agent.eval.sweep import run_sweep
from shelf_agent.utils.jsonl import write_jsonl

CONFIG = load_config("configs/real_screw.yaml")


class FixedPolicy:
    """按固定脚本发调用（用来构造特定 outcome）。"""

    name = "fixed"

    def __init__(self, calls: Sequence[ToolCall]) -> None:
        self._calls = list(calls)
        self._index = 0

    def start(self, info: EpisodeInfo) -> None:
        self._index = 0

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        call = self._calls[min(self._index, len(self._calls) - 1)]
        self._index += 1
        return call


class LoopingPolicy:
    """永远调同一个工具（用来触发预算硬停 / 步数上限）。"""

    name = "looping"

    def __init__(self, call: ToolCall) -> None:
        self._call = call

    def start(self, info: EpisodeInfo) -> None:
        return None

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        return self._call


# ------------------------------------------------------------- outcome 分类


def test_outcome_success() -> None:
    result = run_episode(
        CONFIG,
        seed=0,
        policy_name="rule_oracle",
        failure_script=SCENARIOS["happy"].failure_script,
        failure_rate=0.0,
        verifier_kind="oracle",
    )
    assert result.outcome == "SUCCESS"


def test_outcome_false_finish() -> None:
    result = run_episode(
        CONFIG,
        seed=0,
        policy_name="rule_oracle",
        failure_script=SCENARIOS["verifier_false_positive"].failure_script,
        failure_rate=0.0,
        verifier_kind="scripted",
        verifier_reports=SCENARIOS["verifier_false_positive"].verifier_reports,
    )
    assert result.outcome == "FALSE_FINISH"
    assert result.metrics["unsafe_release"] is True


def test_outcome_safe_abort() -> None:
    result = run_episode(
        CONFIG,
        seed=0,
        policy_name="rule_oracle",
        failure_script=SCENARIOS["stuck_forever"].failure_script,
        failure_rate=0.0,
        verifier_kind="oracle",
    )
    assert result.outcome == "SAFE_ABORT"


def test_outcome_missed_success() -> None:
    """货真价实搬上去了，却报了失败 —— MISSED_SUCCESS。"""
    policy = FixedPolicy(
        [
            ToolCall("robot1_install_screws"),
            ToolCall("robot2_carry_box"),
            ToolCall("finish", {"success": False, "summary": "我以为失败了"}),
        ]
    )
    result = run_episode(CONFIG, seed=0, policy_name="fixed", policy=policy, failure_rate=0.0)
    assert result.outcome == "MISSED_SUCCESS"


def test_outcome_aborted_budget() -> None:
    policy = LoopingPolicy(ToolCall("robot1_install_screws"))
    result = run_episode(CONFIG, seed=0, policy_name="looping", policy=policy, failure_rate=0.0)
    assert result.outcome == "ABORTED_BUDGET"
    assert result.metrics["n_budget_rejections"] >= CONFIG.budget.hard_extra


def test_outcome_max_steps() -> None:
    policy = LoopingPolicy(ToolCall("verify_box"))
    result = run_episode(CONFIG, seed=0, policy_name="looping", policy=policy, failure_rate=0.0)
    assert result.outcome == "MAX_STEPS"
    assert result.metrics["n_steps"] == CONFIG.budget.soft_limit + CONFIG.budget.hard_extra


def test_outcome_agent_error() -> None:
    assert (
        classify_outcome(finish_call=None, truth_ok=False, agent_error=True) == "AGENT_ERROR"
    )


def test_classify_outcome_table() -> None:
    finish_true = ToolCall("finish", {"success": True, "summary": ""})
    finish_false = ToolCall("finish", {"success": False, "summary": ""})
    assert classify_outcome(finish_true, truth_ok=True) == "SUCCESS"
    assert classify_outcome(finish_true, truth_ok=False) == "FALSE_FINISH"
    assert classify_outcome(finish_false, truth_ok=True) == "MISSED_SUCCESS"
    assert classify_outcome(finish_false, truth_ok=False) == "SAFE_ABORT"
    assert classify_outcome(None, truth_ok=False, budget_aborted=True) == "ABORTED_BUDGET"
    assert classify_outcome(None, truth_ok=False, max_steps_reached=True) == "MAX_STEPS"


# ------------------------------------------------------------------ 指标聚合


def test_aggregate_and_counts() -> None:
    results = [
        run_episode(
            CONFIG,
            seed=seed,
            policy_name="rule_oracle",
            failure_script=SCENARIOS["happy"].failure_script,
            failure_rate=0.0,
            verifier_kind="oracle",
        )
        for seed in range(4)
    ]
    counts = outcome_counts(results)
    assert set(counts) == set(OUTCOMES)
    assert counts["SUCCESS"] == 4
    rates = outcome_rates(results)
    assert rates["SUCCESS"]["rate"] == 1.0
    assert rates["SUCCESS"]["ci_low"] <= 1.0
    summary = aggregate(results)
    assert summary["n_episodes"] == 4
    assert summary["metrics"]["makespan_s"]["mean"] > 0
    assert summary["unsafe_release_rate"]["count"] == 0


def test_paired_comparison_uses_the_same_seeds() -> None:
    def run(policy_name: str):
        return [
            run_episode(CONFIG, seed=seed, policy_name=policy_name, failure_rate=0.2)
            for seed in range(10)
        ]

    comparison = paired_compare(run("rule_oracle"), run("naive"), "rule_oracle", "naive")
    assert comparison["paired_seeds"] == 10
    assert comparison["wins"] + comparison["losses"] + comparison["ties"] == 10
    assert comparison["success_rate_right"] <= comparison["success_rate_left"]


# ------------------------------------------------------------------ 端到端


def test_small_sweep_writes_all_files(tmp_path: Path) -> None:
    out = tmp_path / "results"
    sweep = run_sweep(
        CONFIG,
        seeds=5,
        failure_rates=[0.0, 0.2],
        policies=["naive", "rule_oracle"],
        out_dir=out,
    )
    assert len(sweep.results) == 20
    for name in ("episodes.jsonl", "summary.csv", "summary.md", "success_vs_failure_rate.png"):
        path = out / name
        assert path.is_file(), f"缺少 {name}"
        assert path.stat().st_size > 0, f"{name} 是空的"
    with (out / "summary.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4
    assert {row["policy"] for row in rows} == {"naive", "rule_oracle"}


def test_same_seed_produces_identical_episodes_jsonl(tmp_path: Path) -> None:
    """同 seed 两次运行必须逐字节相同。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    for out in (first, second):
        run_sweep(
            CONFIG,
            seeds=4,
            failure_rates=[0.0, 0.2],
            policies=["rule_oracle"],
            out_dir=out,
        )
    assert (first / "episodes.jsonl").read_bytes() == (second / "episodes.jsonl").read_bytes()
    assert (first / "summary.csv").read_bytes() == (second / "summary.csv").read_bytes()


def test_episode_jsonl_has_the_required_fields(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    result = run_episode(CONFIG, seed=0, policy_name="rule_oracle", failure_rate=0.0)
    write_jsonl(path, [result.to_dict()])
    from shelf_agent.utils.jsonl import read_jsonl

    rows = read_jsonl(path)
    assert len(rows) == 1
    row = rows[0]
    for key in (
        "episode_id", "seed", "config_hash", "policy", "outcome", "metrics", "steps", "truth_trace",
    ):
        assert key in row, f"缺少字段 {key}"
    assert isinstance(row["steps"], list) and row["steps"]
    assert isinstance(row["truth_trace"], list)


# ----------------------------------------------------------------------- CLI


def test_cli_list_scenarios(capsys) -> None:
    assert main(["list-scenarios"]) == 0
    out = capsys.readouterr().out
    for name in SCENARIOS:
        assert name in out


def test_cli_demo_exit_code_and_content(capsys) -> None:
    assert main(["demo", "--scenario", "stuck_once", "--show-truth", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "robot1_install_screws" in out
    assert "reset" in out
    assert "verify_box" in out
    assert "SUCCESS" in out
    assert "真值" in out


def test_cli_demo_unknown_scenario_fails_gracefully(capsys) -> None:
    assert main(["demo", "--scenario", "nope"]) == 2


def test_cli_sweep_small(tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "cli_sweep"
    code = main(
        [
            "sweep",
            "--seeds", "3",
            "--failure-rates", "0,0.2",
            "--policies", "naive,rule_oracle",
            "--out", str(out_dir),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "episodes.jsonl" in printed
    assert (out_dir / "episodes.jsonl").is_file()


def test_format_summary_markdown() -> None:
    from shelf_agent.eval.metrics import format_summary_markdown

    results = [
        run_episode(CONFIG, seed=seed, policy_name="rule_oracle", failure_rate=0.0)
        for seed in range(3)
    ]
    summary = aggregate(results)
    text = format_summary_markdown({"rule": summary}, title="测试")
    assert "测试" in text
    assert "SUCCESS" in text
    assert "rule" in text


def test_cli_sweep_rejects_empty_arguments(capsys) -> None:
    assert main(["sweep", "--failure-rates", "", "--policies", ""]) == 2
