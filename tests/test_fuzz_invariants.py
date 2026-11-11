"""模糊测试：随机乱调工具也不许把世界玩坏。"""

from __future__ import annotations

import random
from typing import Sequence

import pytest

from shelf_agent.config import load_config
from shelf_agent.contracts.types import EpisodeInfo, ToolCall, ToolResult
from shelf_agent.eval.scenarios import make_episode
from shelf_agent.utils.enums import BoxLocation, RobotState

CONFIG = load_config("configs/real_screw.yaml")
TARGET = CONFIG.target_screws


class FuzzPolicy:
    """随机挑工具、随机填参数（故意会填错）。"""

    name = "fuzz"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)
        self._tools: list = []

    def start(self, info: EpisodeInfo) -> None:
        self._tools = list(info.tools)

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        tool = self._rng.choice(self._tools)
        args: dict[str, object] = {}
        for param in tool.params:
            if param.type == "string":
                args[param.name] = "".join(
                    self._rng.choice("abcXYZ") for _ in range(self._rng.randint(0, 4))
                )
            elif param.type == "int":
                args[param.name] = self._rng.randint(-3, 8)
            elif param.type == "bool":
                args[param.name] = self._rng.choice([True, False])
            elif param.type == "enum":
                args[param.name] = self._rng.choice(list(param.values) + ["bogus"])
        return ToolCall(tool.name, args)


def _check_invariants(episode, previous_time: float, previous_robots: dict[str, str], call_name: str):
    box = episode.world.box("box_1")
    assert 0 <= box.inside <= TARGET, f"箱内螺丝数越界：{box.inside}"
    assert box.outside >= 0, f"箱外散落数为负：{box.outside}"
    assert episode.clock.time_s >= previous_time, "虚拟时钟倒退了"
    assert box.location in (BoxLocation.STATION, BoxLocation.SHELF, BoxLocation.LOST)
    # 箱子恰好在一个地方：货架槽与 location 必须一致
    on_shelf = any(slot == "box_1" for slot in episode.world.shelf_slots.values())
    if box.location is BoxLocation.SHELF:
        assert on_shelf, "箱子在货架上却没有占用槽位"
    else:
        assert not on_shelf, "箱子不在货架上却占着槽位"
    # FAULT 只能被 reset / ask_human 清除
    if call_name not in ("reset", "ask_human"):
        for name, state in episode.world.robot_states.items():
            if previous_robots.get(name) == RobotState.FAULT.value:
                assert state is RobotState.FAULT, f"{name} 的 FAULT 被 {call_name} 悄悄清掉了"


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_fuzz_keeps_the_world_consistent(seed: int) -> None:
    episode = make_episode(CONFIG, seed=seed, failure_rate=0.3, verifier_kind="noisy")
    policy = FuzzPolicy(seed=seed)
    info = EpisodeInfo(
        scenario=CONFIG.scenario,
        target_screws=TARGET,
        tools=tuple(episode.spec),
        limits={},
        inspector_mode=CONFIG.inspector.mode,
    )
    policy.start(info)
    history: list[tuple[ToolCall, ToolResult]] = []
    previous_time = episode.clock.time_s
    previous_robots = {n: s.value for n, s in episode.world.robot_states.items()}
    for _ in range(40):
        call = policy.next_call(history)
        result = episode.executor.execute(call)
        history.append((call, result))
        _check_invariants(episode, previous_time, previous_robots, call.name)
        previous_time = episode.clock.time_s
        previous_robots = {n: s.value for n, s in episode.world.robot_states.items()}


def test_fuzz_does_not_crash_on_garbage_arguments() -> None:
    """乱填参数只应得到 error，不应抛异常。"""
    episode = make_episode(CONFIG, seed=0, failure_rate=0.0, verifier_kind="oracle")
    from shelf_agent.contracts.types import ERR_INVALID_ARGS

    result = episode.executor.execute(ToolCall("reset", {"robot": "not-a-robot"}))
    assert result.status == "error"
    assert result.error_code == ERR_INVALID_ARGS
    result = episode.executor.execute(ToolCall("finish", {"success": "yes", "summary": "x"}))
    assert result.status == "error"
    assert result.error_code == ERR_INVALID_ARGS


def test_clock_never_goes_backwards_under_budget_rejection() -> None:
    episode = make_episode(CONFIG, seed=0, failure_rate=0.0, verifier_kind="oracle")
    episode.budget.soft_limit = 0
    before = episode.clock.time_s
    episode.executor.execute(ToolCall("robot1_install_screws"))
    assert episode.clock.time_s == before
