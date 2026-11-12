"""随机流纯度（§B1）：同一条流上的抽取次数必须是固定的。

「换策略不会改变某技能第 n 次调用的故障命运」这条不变量，只有在**每次调用
抽取的随机数个数固定**时才成立——否则同一条流的后续位置会依赖上下文。
"""

from __future__ import annotations

import random

import pytest

from shelf_agent.config import FailureSkillConfig, load_config
from shelf_agent.env.failures import RandomFailureModel
from shelf_agent.eval.runner import run_episode
from shelf_agent.perception.imagelib import SyntheticImageLibrary
from shelf_agent.perception.verifier import NoisyVerifier

CONFIG = load_config("configs/real_screw.yaml")

SKILLS = {
    "robot1_install_screws": FailureSkillConfig(weights={"STUCK": 0.4, "INCOMPLETE": 0.4, "STRAY": 0.2}),
    "robot2_carry_box": FailureSkillConfig(weights={"TIMEOUT": 0.5, "SILENT_FAIL": 0.5}),
}

#: 两套不同的 missing 上下文（前者恒定，后者剧烈变化）。
CONTEXT_A = [6] * 50
CONTEXT_B = [6, 3, 2, 5, 4, 1]


class CountingRng:
    """只允许 ``random()`` 的随机流：其余抽法一律报错。"""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)
        self.count = 0

    def random(self) -> float:
        self.count += 1
        return self._rng.random()

    def randint(self, *args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("不得使用 randint（抽取次数随参数变化，会破坏纯度）")

    def uniform(self, *args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("不得使用 uniform")

    def choice(self, *args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("不得使用 choice")


def _kinds(model: RandomFailureModel, contexts: list[int], skill: str) -> list[str]:
    out: list[str] = []
    for index, missing in enumerate(contexts):
        event = model.next_event(skill, index, {"missing": missing})
        out.append(event.kind if event is not None else "none")
    return out


@pytest.mark.parametrize("rate", [0.1, 0.3, 0.6])
def test_failure_fate_is_independent_of_context(rate: float) -> None:
    """同一 seed 下，missing 上下文不同，前 50 次的 kind 序列必须逐 seed 相同。"""
    for seed in range(200):
        model_a = RandomFailureModel(seed=seed, rate=rate, skills=SKILLS)
        model_b = RandomFailureModel(seed=seed, rate=rate, skills=SKILLS)
        kinds_a = _kinds(model_a, CONTEXT_A, "robot1_install_screws")
        kinds_b = _kinds(model_b, [CONTEXT_B[i % len(CONTEXT_B)] for i in range(50)], "robot1_install_screws")
        assert kinds_a == kinds_b, f"seed={seed}: 故障命运依赖了上下文"


def test_failure_stream_draws_fixed_count() -> None:
    """每次 next_event 恰抽 3 个随机数，与真值 / kind / 上下文无关。"""
    for rate in (0.0, 0.5, 1.0):
        model = RandomFailureModel(seed=3, rate=rate, skills=SKILLS)
        spy = CountingRng(3)
        model._rngs["robot1_install_screws"] = spy
        model.next_event("robot1_install_screws", 0, {"missing": 6})
        assert spy.count == 3, f"rate={rate} 时抽取次数不是 3"


def test_unconfigured_skill_still_draws_fixed_count() -> None:
    """技能没有配置时也照抽 3 个（流位置不因分支而变）。"""
    model = RandomFailureModel(seed=5, rate=0.5, skills=SKILLS)
    spy = CountingRng(5)
    model._rngs["mystery"] = spy
    model.next_event("mystery", 0, {"missing": 6})
    assert spy.count == 3


def test_verifier_draws_fixed_count() -> None:
    """每次 verify 恰抽 5 个随机数，与真值无关。"""
    library = SyntheticImageLibrary()
    for inside, outside in ((6, 0), (5, 1), (0, 0)):
        verifier = NoisyVerifier(target=6)
        spy = CountingRng(11)
        verifier.verify(library.capture({"inside": inside, "outside": outside}), spy)
        assert spy.count == 5, f"真值 ({inside},{outside}) 时抽取次数不是 5"


def test_paired_seeds_share_first_events() -> None:
    """同 seed 下，naive 与 rule_oracle 的第一次 install / carry 事件必须相同。"""
    for seed in range(50):
        naive = run_episode(CONFIG, seed=seed, policy_name="naive", failure_rate=0.4)
        rule = run_episode(CONFIG, seed=seed, policy_name="rule_oracle", failure_rate=0.4)
        assert _first_event(naive.steps, "robot1_install_screws") == _first_event(
            rule.steps, "robot1_install_screws"
        ), f"seed={seed}: 第一次 install 的故障事件不一致"
        assert _first_event(naive.steps, "robot2_carry_box") == _first_event(
            rule.steps, "robot2_carry_box"
        ), f"seed={seed}: 第一次 carry 的故障事件不一致"


def _first_event(steps: list[dict], tool: str) -> str:
    """某工具第一次调用时注入的事件（真值通道）。"""
    for step in steps:
        if step["call"]["name"] != tool:
            continue
        event = (step.get("truth_snapshot") or {}).get("last_event")
        if not event:
            return "none"
        return str(event.get("kind"))
    return "never-called"
