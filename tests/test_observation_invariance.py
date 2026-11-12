"""观测不变性（§A3）：同一 status 下，不同隐藏状态必须产生**逐字相同**的可观测结果。

这是「必须验证才能发现隐藏故障」这个实验前提的守卫：install / carry 的返回
只有 status 与耗时，文案不得泄露装没装满、散没散落、箱子在哪。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

import pytest

from shelf_agent.agents.rule_selfheal import RuleSelfHealPolicy
from shelf_agent.config import load_config
from shelf_agent.contracts.types import BoxLocation, RobotState, ToolCall, ToolResult
from shelf_agent.eval.runner import build_info, run_episode
from shelf_agent.eval.scenarios import SCENARIOS, Scenario, make_episode

CONFIG = load_config("configs/real_screw.yaml")

#: 期望的文案（黑盒口径：本文件不 import 后端的常量，这样它也能对着旧实现跑，
#: 在修复前给出「红」）。
EXPECT_DONE = "done"
EXPECT_TIMEOUT = "timeout"

#: executor 层产生的错误码（不是后端文案，不参与白名单）。
EXECUTOR_ERROR_CODES = frozenset(
    {"UNKNOWN_TOOL", "INVALID_ARGS", "BUDGET_EXCEEDED", "MAX_STEPS", "EPISODE_FINISHED"}
)


def _obs(result: ToolResult) -> dict[str, Any]:
    """可观测结果（去掉带抖动的耗时）。"""
    payload = result.to_dict()
    payload.pop("duration_s")
    return payload


def _run(
    tool: str,
    script: Mapping[str, Any] | None = None,
    pre: Callable[[Any], None] | None = None,
    args: Mapping[str, Any] | None = None,
):
    """跑一次技能调用（直接打后端，绕开 executor）。"""
    ep = make_episode(
        CONFIG,
        seed=0,
        failure_script=script,
        failure_rate=0.0,
        verifier_kind="oracle",
    )
    if pre is not None:
        pre(ep)
    return ep.backend.invoke(tool, dict(args or {}))


# --------------------------------------------------------------- install done


def test_install_done_is_indistinguishable() -> None:
    """正常装满 / 装少了 / 掉在箱外，agent 看到的必须一模一样。"""
    base = _obs(_run("robot1_install_screws"))
    assert base["status"] == "done"
    assert base["message"] == EXPECT_DONE
    for script in (
        {"robot1_install_screws": [("INCOMPLETE", {"progress": 3})]},
        {"robot1_install_screws": ["STRAY"]},
    ):
        assert _obs(_run("robot1_install_screws", script)) == base


# ------------------------------------------------------------- install timeout


def _fault_robot1(ep) -> None:
    ep.world.set_robot("robot1", RobotState.FAULT)


def _box_not_at_station(ep) -> None:
    ep.world.box("box_1").location = BoxLocation.SHELF


def test_install_timeout_is_indistinguishable() -> None:
    """真卡住 / 机器人故障 / 箱子不在工位，agent 看到的必须一模一样。"""
    base = _obs(_run("robot1_install_screws", {"robot1_install_screws": ["STUCK"]}))
    assert base["status"] == "timeout"
    assert base["message"] == EXPECT_TIMEOUT
    assert _obs(_run("robot1_install_screws", pre=_fault_robot1)) == base
    assert _obs(_run("robot1_install_screws", pre=_box_not_at_station)) == base


def test_install_timeout_duration_is_the_contract_timeout() -> None:
    result = _run("robot1_install_screws", {"robot1_install_screws": ["STUCK"]})
    assert result.duration_s == CONFIG.tools_section[0]["timeout_s"]


# ---------------------------------------------------------------- carry done


def test_carry_done_is_indistinguishable() -> None:
    """正常搬运 / 静默失败（箱子没动），agent 看到的必须一模一样。"""
    base = _obs(_run("robot2_carry_box"))
    assert base["status"] == "done"
    assert base["message"] == EXPECT_DONE
    silent = _obs(_run("robot2_carry_box", {"robot2_carry_box": ["SILENT_FAIL"]}))
    assert silent == base


# ------------------------------------------------------------- carry timeout


def _fault_robot2(ep) -> None:
    ep.world.set_robot("robot2", RobotState.FAULT)


def _no_free_slot(ep) -> None:
    ep.world.shelf_slots["slot_1"] = "box_1"


def test_carry_timeout_is_indistinguishable() -> None:
    """超时（丢箱 / 不丢箱）/ 无空槽 / robot2 故障 / 箱子不在工位，全部一致。"""
    base = _obs(_run("robot2_carry_box", {"robot2_carry_box": [("TIMEOUT", {"lost": True})]}))
    assert base["status"] == "timeout"
    assert base["message"] == EXPECT_TIMEOUT
    variants = (
        {"robot2_carry_box": [("TIMEOUT", {"lost": False})]},
        {"robot2_carry_box": [("TIMEOUT", {"lost": True})]},
    )
    for script in variants:
        assert _obs(_run("robot2_carry_box", script)) == base
    assert _obs(_run("robot2_carry_box", pre=_no_free_slot)) == base
    assert _obs(_run("robot2_carry_box", pre=_fault_robot2)) == base
    assert _obs(_run("robot2_carry_box", pre=_box_not_at_station)) == base


# ------------------------------------------------------------- 文案白名单


@pytest.mark.parametrize("policy_name", ["naive", "rule_noisy"])
@pytest.mark.parametrize("rate", [0.1, 0.3, 0.6])
def test_messages_are_a_function_of_status_only(policy_name: str, rate: float) -> None:
    """按 (tool, status, error_code) 收集真实出现的 message，每组必须只有一个值。"""
    seen: dict[tuple[str, str, str | None], set[str]] = {}
    for scenario in SCENARIOS.values():
        result = run_episode(
            CONFIG,
            seed=0,
            policy_name=policy_name,
            failure_script=scenario.failure_script,
            failure_rate=scenario.failure_rate,
            verifier_kind=scenario.verifier_kind,
            verifier_reports=scenario.verifier_reports,
        )
        _collect(seen, result.steps)
    for seed in range(200):
        result = run_episode(CONFIG, seed=seed, policy_name=policy_name, failure_rate=rate)
        _collect(seen, result.steps)
    offenders = {
        key: sorted(messages) for key, messages in seen.items() if len(messages) > 1
    }
    assert not offenders, f"同一 (tool, status, error_code) 出现了多种文案：{offenders}"


def _collect(
    seen: dict[tuple[str, str, str | None], set[str]],
    steps: list[Mapping[str, Any]],
) -> None:
    for step in steps:
        tool = step["call"]["name"]
        if tool == "ask_human":
            continue  # 人类说的话，本来就会因情况而异
        result = step["result"]
        code = result.get("error_code")
        if code in EXECUTOR_ERROR_CODES:
            continue
        key = (tool, result["status"], code)
        seen.setdefault(key, set()).add(result.get("message") or "")


# --------------------------------------------------------- 策略不读 message


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_policy_sequence_does_not_depend_on_message(name: str) -> None:
    """把每个结果的 message 清空后重放，调用序列必须完全相同。"""
    scenario: Scenario = SCENARIOS[name]
    original, results = _run_policy(scenario)
    blanked = [
        (call, replace(result, message="")) for call, result in zip(original, results)
    ]
    replayed = _replay(scenario, blanked)
    expected = [(call.name, dict(call.args)) for call in original]
    assert replayed == expected, f"{name} 的调用序列依赖了 message"


def _run_policy(scenario: Scenario) -> tuple[list[ToolCall], list[ToolResult]]:
    """跑一遍规则策略，返回调用序列与结果序列。"""
    ep = make_episode(
        CONFIG,
        seed=0,
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )
    info = build_info(CONFIG, ep)
    policy = RuleSelfHealPolicy()
    policy.start(info)
    calls: list[ToolCall] = []
    results: list[ToolResult] = []
    history: list[tuple[ToolCall, ToolResult]] = []
    for _ in range(60):
        call = policy.next_call(history)
        result = ep.executor.execute(call)
        calls.append(call)
        results.append(result)
        history.append((call, result))
        if call.name == "finish":
            break
    return calls, results


def _replay(
    scenario: Scenario,
    history: list[tuple[ToolCall, ToolResult]],
) -> list[tuple[str, dict[str, Any]]]:
    """用给定的历史重放策略，返回它依次给出的调用。"""
    ep = make_episode(
        CONFIG,
        seed=0,
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )
    info = build_info(CONFIG, ep)
    policy = RuleSelfHealPolicy()
    policy.start(info)
    out: list[tuple[str, dict[str, Any]]] = []
    for index in range(len(history)):
        call = policy.next_call(history[:index])
        out.append((call.name, dict(call.args)))
    return out
