"""策略单测：每个脚本场景的精确调用序列、限额行为、以及「没验证完不许搬」的硬约束。"""

from __future__ import annotations

import pytest

from shelf_agent.config import load_config
from shelf_agent.eval.runner import run_episode
from shelf_agent.eval.scenarios import SCENARIOS, Scenario

CONFIG = load_config("configs/real_screw.yaml")
TARGET = CONFIG.target_screws


def _run(scenario: Scenario, seed: int = 0, policy_name: str = "rule_oracle"):
    return run_episode(
        CONFIG,
        seed=seed,
        policy_name=policy_name,
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )


def _sequence(result) -> tuple[str, ...]:
    return tuple(step["call"]["name"] for step in result.steps)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_sequence_and_outcome(name: str) -> None:
    """每个场景的精确工具调用序列与最终 outcome。"""
    scenario = SCENARIOS[name]
    result = _run(scenario)
    assert _sequence(result) == scenario.expected_sequence, f"{name} 的调用序列不符"
    assert result.outcome == scenario.expected_outcome


def test_stuck_once_resets_robot1_then_topups() -> None:
    result = _run(SCENARIOS["stuck_once"])
    resets = [s for s in result.steps if s["call"]["name"] == "reset"]
    assert len(resets) == 1
    assert resets[0]["call"]["args"] == {"robot": "robot1"}
    assert result.outcome == "SUCCESS"


def test_happy_path_finishes_with_success() -> None:
    result = _run(SCENARIOS["happy"])
    finish = result.steps[-1]
    assert finish["call"]["name"] == "finish"
    assert finish["call"]["args"]["success"] is True


def test_stuck_forever_respects_all_limits() -> None:
    """限额耗尽 → 诚实放弃，不装无限循环。"""
    result = _run(SCENARIOS["stuck_forever"])
    counts: dict[str, int] = {}
    for step in result.steps:
        counts[step["call"]["name"]] = counts.get(step["call"]["name"], 0) + 1
    assert counts["robot1_install_screws"] == CONFIG.rule_policy.max_install_calls
    assert counts["reset"] == CONFIG.rule_policy.max_resets_per_robot
    assert counts["ask_human"] == CONFIG.rule_policy.max_human_asks
    assert result.outcome == "SAFE_ABORT"
    assert result.metrics["unsafe_release"] is False
    finish = result.steps[-1]
    assert finish["call"]["args"]["success"] is False


def test_carry_attempt_limit_is_respected() -> None:
    scenario = Scenario(
        name="silent_thrice",
        description="搬运连续三次静默失败",
        failure_script={"robot2_carry_box": ["SILENT_FAIL"] * 3},
    )
    result = _run(scenario)
    carries = [s for s in result.steps if s["call"]["name"] == "robot2_carry_box"]
    assert len(carries) == CONFIG.rule_policy.max_carry_attempts
    assert result.outcome == "SAFE_ABORT"


def test_after_ask_human_the_next_call_is_verify() -> None:
    """HUMAN 之后一律回 VERIFY 重新检查。"""
    result = _run(SCENARIOS["stray_needs_human"])
    names = _sequence(result)
    for index, name in enumerate(names):
        if name == "ask_human":
            assert names[index + 1] == "verify_box"


def test_unclear_report_triggers_retake_then_human() -> None:
    """看不清 → 重拍（≤ max_unclear_retries）→ 仍看不清 → 求助。"""
    scenario = Scenario(
        name="always_unclear",
        description="验证器一直看不清",
        failure_script={},
        verifier_kind="scripted",
        verifier_reports=(
            {"inside": 6, "outside": 0, "clear": False},
            {"inside": 6, "outside": 0, "clear": False},
            {"inside": 6, "outside": 0, "clear": False},
        ),
    )
    result = _run(scenario)
    names = _sequence(result)
    first_human = names.index("ask_human")
    # 首次求助之前：1 次初验 + max_unclear_retries 次重拍
    assert names[:first_human].count("verify_box") == CONFIG.rule_policy.max_unclear_retries + 1
    assert result.outcome == "SAFE_ABORT"


def test_inside_greater_than_target_is_treated_as_unreliable() -> None:
    """inside > N 是自相矛盾的报告 → 重拍。"""
    scenario = Scenario(
        name="too_many",
        description="验证器报出比目标还多的螺丝数",
        failure_script={},
        verifier_kind="scripted",
        verifier_reports=(
            {"inside": 9, "outside": 0, "clear": True},
            {"inside": 9, "outside": 0, "clear": True},
            {"inside": 9, "outside": 0, "clear": True},
        ),
    )
    result = _run(scenario)
    names = _sequence(result)
    first_human = names.index("ask_human")
    assert names[:first_human].count("verify_box") == CONFIG.rule_policy.max_unclear_retries + 1
    assert "robot2_carry_box" not in names


@pytest.mark.parametrize("rate", [0.0, 0.1, 0.3])
def test_carry_only_after_a_complete_report(rate: float) -> None:
    """property 测试：只有在**上一次验证报告 complete** 时才允许搬运。"""
    for seed in range(200):
        result = run_episode(
            CONFIG,
            seed=seed,
            policy_name="rule_noisy",
            failure_rate=rate,
        )
        steps = result.steps
        for index, step in enumerate(steps):
            if step["call"]["name"] != "robot2_carry_box":
                continue
            report = None
            for previous in reversed(steps[:index]):
                if previous["call"]["name"] == "verify_box":
                    report = previous["result"].get("data") or {}
                    break
            assert report is not None, f"seed={seed}: 搬运之前必须先验证"
            assert report.get("clear") is True, f"seed={seed}: 搬运前的报告不清晰"
            assert report.get("inside") == TARGET, f"seed={seed}: 搬运前箱内数不等于 N"
            assert report.get("outside") == 0, f"seed={seed}: 搬运前箱外有散落"


def test_policy_never_sees_truth() -> None:
    """策略拿到的结果里不含真值字段（真值只进轨迹快照）。"""
    from shelf_agent.env.sim_backend import ALLOWED_MESSAGES
    for name in sorted(SCENARIOS):
        result = _run(SCENARIOS[name])
        for step in result.steps:
            payload = step["result"]
            assert set(payload) == {"status", "error_code", "message", "duration_s", "data"}
            assert "truth_snapshot" not in payload
            if step["call"]["name"] != "ask_human":
                assert payload["message"] in ALLOWED_MESSAGES, (
                    f"{name}: message 泄露了隐藏状态：{payload['message']!r}"
                )


# --------------------------------------------------------- 巡检机器人与边界分支


def _config_with_inspector(mode: str, false_negative: float = 0.0):
    from shelf_agent.config import build_config

    raw = {
        **CONFIG.raw,
        "inspector": {
            **CONFIG.raw["inspector"],
            "mode": mode,
            "false_negative": false_negative,
        },
    }
    return build_config(raw)


def test_shelf_status_inspector_confirms_the_delivery() -> None:
    """货架可看时：搬完用巡检机器人确认在架上才收工。"""
    from shelf_agent.eval.runner import run_episode as _run_episode

    config = _config_with_inspector("shelf_status")
    result = _run_episode(
        config,
        seed=0,
        policy_name="rule_oracle",
        failure_script=SCENARIOS["happy"].failure_script,
        failure_rate=0.0,
        verifier_kind="oracle",
    )
    names = _sequence(result)
    assert "inspector_run" in names
    assert names[-1] == "finish"
    assert result.outcome == "SUCCESS"


def test_shelf_status_inspector_missing_box_asks_human() -> None:
    """巡检说箱子不在架上 → 求助人类 → 人类也帮不上 → 诚实放弃。

    注意结局是 MISSED_SUCCESS：箱子其实已经在架上（真值成功），但策略拿不到
    证据，于是诚实报告失败——这正是「宁可失败也不谎报」的行为。
    """
    from shelf_agent.eval.runner import run_episode as _run_episode

    config = _config_with_inspector("shelf_status", false_negative=1.0)
    result = _run_episode(
        config,
        seed=0,
        policy_name="rule_oracle",
        failure_script=SCENARIOS["happy"].failure_script,
        failure_rate=0.0,
        verifier_kind="oracle",
    )
    names = _sequence(result)
    assert "inspector_run" in names
    assert "ask_human" in names
    assert result.steps[-1]["call"]["args"]["success"] is False
    assert result.outcome == "MISSED_SUCCESS"


def test_human_quota_exhausted_ends_with_honest_failure() -> None:
    """人类的配额没了（ask_human 报错）→ 策略诚实收尾，不硬撑。"""
    from shelf_agent.config import build_config
    from shelf_agent.eval.runner import run_episode as _run_episode

    raw = {
        **CONFIG.raw,
        "human": {**CONFIG.raw["human"], "max_asks": 0},
        "rule_policy": {**CONFIG.raw["rule_policy"], "max_human_asks": 2},
    }
    strict = build_config(raw)
    result = _run_episode(
        strict,
        seed=0,
        policy_name="rule_oracle",
        failure_script=SCENARIOS["stray_needs_human"].failure_script,
        failure_rate=0.0,
        verifier_kind="oracle",
    )
    names = _sequence(result)
    assert "ask_human" in names
    assert result.steps[-1]["call"]["args"]["success"] is False
    assert result.outcome == "SAFE_ABORT"


def test_budget_rejection_during_install_ends_honestly() -> None:
    """预算拒绝物理技能 → 策略诚实收尾，不会假装成功。"""
    from shelf_agent.eval.runner import run_episode as _run_episode

    config = _config_with_inspector("noop")
    # soft_limit 必须 >= 1（配置校验要求）。用 2：install/verify 正常执行，第 3 次
    # 调用（carry）被预算拒绝，策略随后诚实 finish(False)。
    # 步数上限 = soft_limit + hard_extra = 4，刚好容得下那一次 finish。
    raw = {**config.raw, "budget": {**config.raw["budget"], "soft_limit": 2, "hard_extra": 2}}
    from shelf_agent.config import build_config

    strict = build_config(raw)
    result = _run_episode(strict, seed=0, policy_name="rule_oracle", failure_rate=0.0, verifier_kind="oracle")
    assert result.outcome in ("ABORTED_BUDGET", "SAFE_ABORT")
    if result.outcome == "SAFE_ABORT":
        assert result.steps[-1]["call"]["args"]["success"] is False
