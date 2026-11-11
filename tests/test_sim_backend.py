"""仿真后端单测：§3.2 每条语义规则至少一个用例。"""

from __future__ import annotations

import pytest

from shelf_agent.config import load_config
from shelf_agent.contracts.types import ERR_BOX_NOT_AT_STATION, BoxLocation, RobotState
from shelf_agent.eval.scenarios import Episode, make_episode

CONFIG = load_config("configs/real_screw.yaml")
TARGET = CONFIG.target_screws


def episode(script=None, rate=0.0, verifier_kind="oracle", **kwargs) -> Episode:
    return make_episode(
        CONFIG,
        seed=0,
        failure_script=script,
        failure_rate=rate,
        verifier_kind=verifier_kind,
        **kwargs,
    )


# --------------------------------------------------------------------- install


def test_install_without_fault_fills_to_target() -> None:
    ep = episode()
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "done"
    assert result.data == {}
    assert ep.world.box("box_1").inside == TARGET
    assert 45 * 0.8 <= result.duration_s <= 45 * 1.2


def test_install_when_robot_faulted_looks_stuck_and_changes_nothing() -> None:
    """A2：前置不满足（含 FAULT）表现为 timeout 且不改变状态。"""
    ep = episode()
    ep.world.set_robot("robot1", RobotState.FAULT)
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "timeout"
    assert result.duration_s == 120
    assert ep.world.box("box_1").inside == 0
    assert ep.world.robot("robot1") is RobotState.FAULT


def test_install_when_box_not_at_station_looks_stuck() -> None:
    ep = episode()
    ep.world.box("box_1").location = BoxLocation.SHELF
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "timeout"
    assert ep.world.box("box_1").location is BoxLocation.SHELF


def test_install_stuck_sets_fault_and_partial_progress() -> None:
    ep = episode(script={"robot1_install_screws": [("STUCK", {"progress": 2})]})
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "timeout"
    assert result.duration_s == 120
    assert ep.world.box("box_1").inside == 2
    assert ep.world.robot("robot1") is RobotState.FAULT


def test_install_incomplete_returns_done_with_partial_progress() -> None:
    ep = episode(script={"robot1_install_screws": [("INCOMPLETE", {"progress": 3})]})
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "done"
    assert 45 * 0.8 <= result.duration_s <= 45 * 1.2
    assert ep.world.box("box_1").inside == 3


def test_install_stray_leaves_one_screw_outside() -> None:
    ep = episode(script={"robot1_install_screws": ["STRAY"]})
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "done"
    box = ep.world.box("box_1")
    assert box.inside == TARGET - 1
    assert box.outside == 1


def test_install_when_already_full_treats_event_as_no_event() -> None:
    """missing == 0 时故障事件按无事件处理（不会把机器人打成 FAULT）。"""
    ep = episode(script={"robot1_install_screws": ["STUCK"]})
    ep.world.box("box_1").inside = TARGET
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "done"
    assert ep.world.robot("robot1") is RobotState.OK
    assert ep.world.box("box_1").inside == TARGET


# ---------------------------------------------------------------------- carry


def test_carry_without_fault_moves_box_to_shelf() -> None:
    ep = episode()
    ep.world.box("box_1").inside = TARGET
    result = ep.backend.invoke("robot2_carry_box", {})
    assert result.status == "done"
    assert ep.world.box("box_1").location is BoxLocation.SHELF
    assert ep.world.shelf_slots["slot_1"] == "box_1"


def test_carry_when_robot2_faulted_looks_stuck() -> None:
    ep = episode()
    ep.world.set_robot("robot2", RobotState.FAULT)
    result = ep.backend.invoke("robot2_carry_box", {})
    assert result.status == "timeout"
    assert result.duration_s == 180
    assert ep.world.box("box_1").location is BoxLocation.STATION


def test_carry_timeout_marks_robot2_fault_and_keeps_box() -> None:
    ep = episode(script={"robot2_carry_box": [("TIMEOUT", {"lost": False})]})
    result = ep.backend.invoke("robot2_carry_box", {})
    assert result.status == "timeout"
    assert result.duration_s == 180
    assert ep.world.robot("robot2") is RobotState.FAULT
    assert ep.world.box("box_1").location is BoxLocation.STATION


def test_carry_timeout_can_lose_the_box() -> None:
    ep = episode(script={"robot2_carry_box": [("TIMEOUT", {"lost": True})]})
    result = ep.backend.invoke("robot2_carry_box", {})
    assert result.status == "timeout"
    assert ep.world.box("box_1").location is BoxLocation.LOST


def test_carry_silent_fail_returns_done_but_box_stays() -> None:
    """A4/A16：SILENT_FAIL 返回 done，箱子仍在工位，robot2 不进 FAULT。"""
    ep = episode(script={"robot2_carry_box": ["SILENT_FAIL"]})
    result = ep.backend.invoke("robot2_carry_box", {})
    assert result.status == "done"
    assert ep.world.box("box_1").location is BoxLocation.STATION
    assert ep.world.robot("robot2") is RobotState.OK


def test_unsafe_release_recorded_when_carrying_incomplete_box() -> None:
    ep = episode()
    ep.world.box("box_1").inside = TARGET - 1
    ep.backend.invoke("robot2_carry_box", {})
    assert ep.backend.metrics.unsafe_release is True


def test_unsafe_release_false_when_box_is_complete() -> None:
    ep = episode()
    ep.world.box("box_1").inside = TARGET
    ep.backend.invoke("robot2_carry_box", {})
    assert ep.backend.metrics.unsafe_release is False


# ---------------------------------------------------------------------- reset


def test_reset_clears_fault_and_takes_configured_time() -> None:
    ep = episode()
    ep.world.set_robot("robot1", RobotState.FAULT)
    result = ep.backend.invoke("reset", {"robot": "robot1"})
    assert result.status == "ok"
    assert result.duration_s == 20
    assert ep.world.robot("robot1") is RobotState.OK


def test_reset_all_takes_max_duration_and_fixes_both() -> None:
    ep = episode()
    ep.world.set_robot("robot1", RobotState.FAULT)
    ep.world.set_robot("robot2", RobotState.FAULT)
    result = ep.backend.invoke("reset", {"robot": "all"})
    assert result.duration_s == 30
    assert ep.world.robot("robot1") is RobotState.OK
    assert ep.world.robot("robot2") is RobotState.OK


def test_reset_does_not_touch_the_box() -> None:
    ep = episode()
    ep.world.box("box_1").inside = 3
    ep.world.box("box_1").outside = 1
    ep.backend.invoke("reset", {"robot": "robot1"})
    box = ep.world.box("box_1")
    assert box.inside == 3 and box.outside == 1


def test_reset_of_healthy_robot_counts_as_unnecessary() -> None:
    ep = episode()
    ep.backend.invoke("reset", {"robot": "robot1"})
    assert ep.backend.metrics.n_unnecessary_resets == 1
    assert ep.backend.metrics.n_resets == 1


# --------------------------------------------------------------------- verify


def test_verify_reports_counts_and_uses_three_seconds() -> None:
    ep = episode()
    ep.world.box("box_1").inside = 4
    ep.world.box("box_1").outside = 1
    result = ep.backend.invoke("verify_box", {})
    assert result.status == "ok"
    assert result.duration_s == 3
    assert result.data == {"inside": 4, "outside": 1, "clear": True}


def test_verify_errors_when_box_not_at_station() -> None:
    ep = episode()
    ep.world.box("box_1").location = BoxLocation.SHELF
    result = ep.backend.invoke("verify_box", {})
    assert result.status == "error"
    assert result.error_code == ERR_BOX_NOT_AT_STATION
    assert result.duration_s == 1


def test_head_image_returns_only_reference() -> None:
    ep = episode()
    result = ep.backend.invoke("robot1_get_head_image", {})
    assert result.status == "ok"
    assert set(result.data) == {"image_id", "path"}


def test_head_image_errors_when_box_not_at_station() -> None:
    ep = episode()
    ep.world.box("box_1").location = BoxLocation.LOST
    result = ep.backend.invoke("robot1_get_head_image", {})
    assert result.error_code == ERR_BOX_NOT_AT_STATION


# ------------------------------------------------------------------ inspector


def test_inspector_noop_mode_returns_done_without_data() -> None:
    ep = episode()
    result = ep.backend.invoke("inspector_run", {})
    assert result.status == "done"
    assert result.data == {}
    assert result.duration_s == 40


def test_inspector_shelf_status_mode_reports_truth() -> None:
    config = load_config("configs/real_screw.yaml")
    raw = {**config.raw, "inspector": {**config.raw["inspector"], "mode": "shelf_status"}}
    from shelf_agent.config import build_config

    ep = make_episode(build_config(raw), seed=0, failure_rate=0.0, verifier_kind="oracle")
    assert ep.backend.invoke("inspector_run", {}).data == {"box_on_shelf": False}
    ep.world.box("box_1").location = BoxLocation.SHELF
    assert ep.backend.invoke("inspector_run", {}).data == {"box_on_shelf": True}


def test_inspector_shelf_status_false_negative() -> None:
    from shelf_agent.config import build_config

    config = load_config("configs/real_screw.yaml")
    raw = {
        **config.raw,
        "inspector": {**config.raw["inspector"], "mode": "shelf_status", "false_negative": 1.0},
    }
    ep = make_episode(build_config(raw), seed=0, failure_rate=0.0, verifier_kind="oracle")
    ep.world.box("box_1").location = BoxLocation.SHELF
    assert ep.backend.invoke("inspector_run", {}).data == {"box_on_shelf": False}


# --------------------------------------------------------------- ask_human/finish


def test_ask_human_fixes_stray_screws() -> None:
    ep = episode()
    ep.world.box("box_1").inside = TARGET - 1
    ep.world.box("box_1").outside = 1
    result = ep.backend.invoke("ask_human", {"question": "箱外有螺丝"})
    assert result.status == "ok"
    assert "answer" in result.data
    box = ep.world.box("box_1")
    assert box.inside == TARGET and box.outside == 0


def test_ask_human_quota_exhausted_returns_error_without_time() -> None:
    ep = episode()
    ep.world.human_asks = CONFIG.human.max_asks
    result = ep.backend.invoke("ask_human", {"question": "帮个忙"})
    assert result.status == "error"
    assert result.error_code == "HUMAN_UNAVAILABLE"
    assert result.duration_s == 0


def test_finish_returns_ok() -> None:
    ep = episode()
    result = ep.backend.invoke("finish", {"success": True, "summary": "完成"})
    assert result.status == "ok"


# ------------------------------------------------------------------- 时间累计


def test_clock_accumulates_call_durations() -> None:
    ep = episode()
    start = ep.clock.time_s
    ep.backend.invoke("robot1_install_screws", {})
    after_install = ep.clock.time_s
    assert after_install > start
    ep.backend.invoke("verify_box", {})
    assert ep.clock.time_s == pytest.approx(after_install + 3)


def test_timeout_call_advances_by_timeout_s() -> None:
    ep = episode()
    ep.world.set_robot("robot1", RobotState.FAULT)
    ep.backend.invoke("robot1_install_screws", {})
    assert ep.clock.time_s == 120


def test_backend_reports_unknown_tool_as_error() -> None:
    ep = episode()
    result = ep.backend.invoke("nope", {})
    assert result.status == "error"
    assert result.error_code == "UNKNOWN_TOOL"


def test_unknown_failure_kind_is_treated_as_no_event() -> None:
    """未知故障类型按无事件处理，不该让仿真崩掉。"""
    ep = episode(script={"robot1_install_screws": ["MYSTERY"]})
    result = ep.backend.invoke("robot1_install_screws", {})
    assert result.status == "done"
    assert ep.world.box("box_1").inside == TARGET
    ep2 = episode(script={"robot2_carry_box": ["MYSTERY"]})
    ep2.world.box("box_1").inside = TARGET
    result2 = ep2.backend.invoke("robot2_carry_box", {})
    assert result2.status == "done"
    assert ep2.world.box("box_1").location is BoxLocation.SHELF


def test_carry_without_free_slot_times_out() -> None:
    """货架没有空槽 → 搬运失败（箱子仍在工位，robot2 进 FAULT）。"""
    ep = make_episode(
        CONFIG,
        seed=0,
        failure_rate=0.0,
        verifier_kind="oracle",
        box_ids=("box_1", "box_2"),
        slot_ids=("slot_1",),
    )
    ep.world.box("box_2").location = BoxLocation.SHELF
    ep.world.shelf_slots["slot_1"] = "box_2"
    result = ep.backend.invoke("robot2_carry_box", {})
    assert result.status == "timeout"
    assert ep.world.box("box_1").location is BoxLocation.STATION
    assert ep.world.robot("robot2") is RobotState.FAULT


def test_reset_all_uses_max_of_individual_durations_when_all_is_absent() -> None:
    from shelf_agent.config import build_config

    raw = {**CONFIG.raw}
    raw["tools"] = [dict(tool) for tool in CONFIG.raw["tools"]]
    for tool in raw["tools"]:
        if tool["name"] == "reset":
            tool["reset_durations"] = {"robot1": 20, "robot2": 45}
    ep = make_episode(build_config(raw), seed=0, failure_rate=0.0, verifier_kind="oracle")
    result = ep.backend.invoke("reset", {"robot": "all"})
    assert result.duration_s == 45
