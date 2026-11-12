"""消融基线 ``retry_only``（§C1）：只看 status，不验证、不求助。"""

from __future__ import annotations

import pytest

from shelf_agent.config import load_config
from shelf_agent.eval.runner import run_episode
from shelf_agent.eval.scenarios import SCENARIOS, Scenario

CONFIG = load_config("configs/real_screw.yaml")


def _run(scenario: Scenario, seed: int = 0):
    return run_episode(
        CONFIG,
        seed=seed,
        policy_name="retry_only",
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )


def _sequence(result) -> tuple[str, ...]:
    return tuple(step["call"]["name"] for step in result.steps)


def test_stuck_once_resets_and_retries() -> None:
    result = _run(SCENARIOS["stuck_once"])
    assert _sequence(result) == (
        "robot1_install_screws",
        "reset",
        "robot1_install_screws",
        "robot2_carry_box",
        "finish",
    )
    assert result.outcome == "SUCCESS"


def test_incomplete_topup_becomes_false_finish() -> None:
    """不验证 → 装少了也照样搬 → 谎报成功。"""
    result = _run(SCENARIOS["incomplete_topup"])
    assert result.outcome == "FALSE_FINISH"
    assert result.metrics["unsafe_release"] is True


def test_lost_box_exhausts_limits_and_aborts() -> None:
    """箱子丢失时它救不回来（不验证就不知道箱子没了）。"""
    result = _run(SCENARIOS["lost_box_needs_human"])
    assert result.outcome == "SAFE_ABORT"
    assert "verify_box" not in _sequence(result)
    assert "ask_human" not in _sequence(result)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_retry_only_never_verifies(name: str) -> None:
    """property：retry_only 的轨迹里永不出现 verify / ask_human / inspector。"""
    result = _run(SCENARIOS[name])
    for step in result.steps:
        assert step["call"]["name"] not in ("verify_box", "ask_human", "inspector_run")


@pytest.mark.parametrize("rate", [0.1, 0.3])
def test_retry_only_never_verifies_under_random_failures(rate: float) -> None:
    for seed in range(20):
        result = run_episode(CONFIG, seed=seed, policy_name="retry_only", failure_rate=rate)
        for step in result.steps:
            assert step["call"]["name"] not in ("verify_box", "ask_human", "inspector_run")


def test_retry_only_respects_limits() -> None:
    """install / reset / carry 的限额都用 rule_policy 里的值。"""
    result = _run(SCENARIOS["stuck_forever"])
    counts: dict[str, int] = {}
    for step in result.steps:
        counts[step["call"]["name"]] = counts.get(step["call"]["name"], 0) + 1
    assert counts["robot1_install_screws"] <= CONFIG.rule_policy.max_install_calls
    assert counts["reset"] <= CONFIG.rule_policy.max_resets_per_robot
    assert result.outcome == "SAFE_ABORT"
