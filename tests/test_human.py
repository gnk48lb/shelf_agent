"""人类求助通道单测：三种修复、无事可做、配额、耗时。"""

from __future__ import annotations

from shelf_agent.env.human import NOTHING_TO_DO, ScriptedHuman
from shelf_agent.env.world import make_world
from shelf_agent.utils.enums import BoxLocation, RobotState


def _world():
    return make_world(["box_1"], ["slot_1"], ["robot1", "robot2"])


def test_human_puts_stray_screws_back_into_box() -> None:
    world = _world()
    world.box("box_1").inside = 5
    world.box("box_1").outside = 1
    answer = ScriptedHuman().ask("箱外有螺丝", world)
    assert answer.status == "ok"
    assert world.box("box_1").inside == 6
    assert world.box("box_1").outside == 0
    assert "散落" in answer.answer


def test_human_returns_lost_box_to_station() -> None:
    world = _world()
    world.box("box_1").location = BoxLocation.LOST
    answer = ScriptedHuman().ask("箱子不见了", world)
    assert answer.status == "ok"
    assert world.box("box_1").location is BoxLocation.STATION
    assert "放回工位" in answer.answer


def test_human_recovers_faulty_robot() -> None:
    world = _world()
    world.set_robot("robot1", RobotState.FAULT)
    answer = ScriptedHuman().ask("robot1 不动", world)
    assert world.robot("robot1") is RobotState.OK
    assert "robot1" in answer.answer


def test_human_handles_all_three_at_once() -> None:
    world = _world()
    world.box("box_1").outside = 2
    world.box("box_1").location = BoxLocation.LOST
    world.set_robot("robot2", RobotState.FAULT)
    answer = ScriptedHuman().ask("全面检查", world)
    assert world.box("box_1").outside == 0
    assert world.box("box_1").location is BoxLocation.STATION
    assert world.robot("robot2") is RobotState.OK
    assert answer.answer.count("；") >= 2


def test_human_reports_nothing_to_do() -> None:
    world = _world()
    answer = ScriptedHuman().ask("还好吗", world)
    assert answer.status == "ok"
    assert answer.answer == NOTHING_TO_DO


def test_human_quota_exhausted() -> None:
    world = _world()
    human = ScriptedHuman(latency_s=120, max_asks=2)
    human.ask("第一次", world)
    human.ask("第二次", world)
    assert world.human_asks == 2
    answer = human.ask("第三次", world)
    assert answer.status == "error"
    assert answer.error_code == "HUMAN_UNAVAILABLE"
    assert answer.duration_s == 0
    assert world.human_asks == 2


def test_human_latency_is_reported() -> None:
    world = _world()
    answer = ScriptedHuman(latency_s=120).ask("帮个忙", world)
    assert answer.duration_s == 120
