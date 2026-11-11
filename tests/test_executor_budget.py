"""执行器与预算单测：非法调用、软/硬停止、契约校验、轨迹真值隔离。"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from shelf_agent.config import load_config
from shelf_agent.contracts.types import (
    ERR_BUDGET_EXCEEDED,
    ERR_INVALID_ARGS,
    ERR_UNKNOWN_TOOL,
    ContractViolation,
    ToolCall,
    ToolResult,
)
from shelf_agent.eval.scenarios import make_episode

CONFIG = load_config("configs/real_screw.yaml")


def _episode():
    return make_episode(CONFIG, seed=0, failure_rate=0.0, verifier_kind="oracle")


def test_unknown_tool_returns_error_without_advancing_clock() -> None:
    ep = _episode()
    result = ep.executor.execute(ToolCall("nope"))
    assert result.status == "error"
    assert result.error_code == ERR_UNKNOWN_TOOL
    assert ep.clock.time_s == 0.0
    assert ep.budget.invalid_calls == 1


def test_invalid_args_returns_error_without_advancing_clock() -> None:
    ep = _episode()
    result = ep.executor.execute(ToolCall("reset", {"robot": "robot9"}))
    assert result.status == "error"
    assert result.error_code == ERR_INVALID_ARGS
    assert ep.clock.time_s == 0.0
    assert ep.budget.invalid_calls == 1


def test_missing_required_arg_is_invalid() -> None:
    ep = _episode()
    result = ep.executor.execute(ToolCall("finish", {"success": True}))
    assert result.error_code == ERR_INVALID_ARGS


def test_valid_call_advances_clock_and_records_trajectory() -> None:
    ep = _episode()
    result = ep.executor.execute(ToolCall("robot1_install_screws"))
    assert result.status == "done"
    assert ep.clock.time_s > 0
    assert len(ep.trajectory.steps) == 1
    assert ep.trajectory.steps[0].call.name == "robot1_install_screws"


def test_soft_budget_stops_physical_skills_but_keeps_others() -> None:
    ep = _episode()
    ep.budget.soft_limit = 1
    ep.executor.execute(ToolCall("verify_box"))
    assert ep.budget.total_calls == 1
    # 第 2 次调用后总数超过软上限，物理技能被拒
    rejected = ep.executor.execute(ToolCall("robot1_install_screws"))
    assert rejected.status == "error"
    assert rejected.error_code == ERR_BUDGET_EXCEEDED
    # 非物理技能仍然可用
    assert ep.executor.execute(ToolCall("verify_box")).status == "ok"
    assert ep.executor.execute(ToolCall("ask_human", {"question": "在吗"})).status == "ok"
    assert ep.executor.execute(ToolCall("finish", {"success": True, "summary": "s"})).status == "ok"


def test_hard_stop_after_hard_extra_rejections() -> None:
    ep = _episode()
    ep.budget.soft_limit = 0
    ep.budget.hard_extra = 2
    ep.executor.execute(ToolCall("robot1_install_screws"))
    assert ep.budget.aborted is False
    ep.executor.execute(ToolCall("robot1_install_screws"))
    assert ep.budget.aborted is True


def test_invalid_calls_limit_triggers_hard_stop() -> None:
    ep = _episode()
    ep.budget.max_invalid_calls = 2
    ep.executor.execute(ToolCall("nope"))
    ep.executor.execute(ToolCall("nope"))
    assert ep.budget.aborted is False
    ep.executor.execute(ToolCall("nope"))
    assert ep.budget.aborted is True


def test_undeclared_data_key_raises_contract_violation() -> None:
    """后端返回契约没声明的 data 键 → ContractViolation。"""

    class BadBackend:
        def invoke(self, tool: str, args: Mapping[str, Any]) -> ToolResult:
            return ToolResult(status="ok", data={"inside": 6, "secret": "truth"})

        def debug_truth(self) -> Mapping[str, Any]:
            return {}

    ep = _episode()
    ep.executor.backend = BadBackend()
    with pytest.raises(ContractViolation) as excinfo:
        ep.executor.execute(ToolCall("verify_box"))
    assert "secret" in str(excinfo.value)


def test_trajectory_contains_truth_but_result_does_not() -> None:
    ep = _episode()
    result = ep.executor.execute(ToolCall("robot1_install_screws"))
    step = ep.trajectory.steps[0]
    assert "truth_snapshot" in step.to_dict()
    assert step.truth_snapshot["boxes"]["box_1"]["inside"] == 6
    # 结果本身不含真值
    assert set(result.data) == set()
    assert "truth_snapshot" not in result.to_dict()
    assert "inside" not in result.to_dict()["data"]


def test_calls_after_finish_are_rejected() -> None:
    ep = _episode()
    ep.executor.execute(ToolCall("finish", {"success": True, "summary": "done"}))
    result = ep.executor.execute(ToolCall("verify_box"))
    assert result.status == "error"
    assert result.error_code == "EPISODE_FINISHED"


def test_budget_counts_all_calls_including_invalid() -> None:
    ep = _episode()
    ep.executor.execute(ToolCall("nope"))
    ep.executor.execute(ToolCall("verify_box"))
    assert ep.budget.total_calls == 2
