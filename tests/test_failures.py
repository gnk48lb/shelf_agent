"""故障注入模型单测：脚本消费顺序、rate 行为、权重分布、流独立性。"""

from __future__ import annotations

from shelf_agent.config import FailureSkillConfig
from shelf_agent.env.failures import (
    FailureEvent,
    FailureModel,
    RandomFailureModel,
    ScriptedFailureModel,
    parse_event,
)


def test_scripted_consumes_in_order() -> None:
    model = FailureModel.scripted(
        {"robot1_install_screws": ["STUCK", None, ("INCOMPLETE", {"progress": 2})]}
    )
    ctx = {"missing": 5}
    first = model.next_event("robot1_install_screws", 0, ctx)
    assert first is not None and first.kind == "STUCK"
    assert first.params["progress"] == 0
    second = model.next_event("robot1_install_screws", 1, ctx)
    assert second is None
    third = model.next_event("robot1_install_screws", 2, ctx)
    assert third is not None and third.kind == "INCOMPLETE"
    assert third.params["progress"] == 2


def test_scripted_exhausted_returns_normal() -> None:
    """脚本耗尽后恢复正常（不再注入事件）。"""
    model = FailureModel.scripted({"robot1_install_screws": ["STUCK"]})
    assert model.next_event("robot1_install_screws", 0, {"missing": 3}) is not None
    assert model.next_event("robot1_install_screws", 1, {"missing": 3}) is None
    assert model.next_event("robot1_install_screws", 2, {"missing": 3}) is None


def test_scripted_incomplete_default_progress_is_missing_minus_one() -> None:
    model = FailureModel.scripted({"robot1_install_screws": ["INCOMPLETE"]})
    event = model.next_event("robot1_install_screws", 0, {"missing": 4})
    assert event is not None and event.params["progress"] == 3


def test_scripted_timeout_default_not_lost() -> None:
    model = FailureModel.scripted({"robot2_carry_box": ["TIMEOUT"]})
    event = model.next_event("robot2_carry_box", 0)
    assert event is not None and event.kind == "TIMEOUT"
    assert event.params["lost"] is False


def test_scripted_other_skill_has_no_event() -> None:
    model = FailureModel.scripted({"robot1_install_screws": ["STUCK"]})
    assert model.next_event("robot2_carry_box", 0) is None


def test_scripted_fallback_used_after_exhaustion() -> None:
    fallback = RandomFailureModel(seed=1, rate=1.0, skills={"robot1_install_screws": FailureSkillConfig(weights={"STUCK": 1.0})})
    model = ScriptedFailureModel({"robot1_install_screws": ["STRAY"]}, fallback=fallback)
    assert (model.next_event("robot1_install_screws", 0) or FailureEvent("")).kind == "STRAY"
    after = model.next_event("robot1_install_screws", 1, {"missing": 3})
    assert after is not None and after.kind == "STUCK"


def test_parse_event_rejects_garbage() -> None:
    try:
        parse_event(123)
    except ValueError as exc:
        assert "无法解析" in str(exc)
    else:  # pragma: no cover - 应当抛异常
        raise AssertionError("非法事件规格应当抛 ValueError")


def test_rate_zero_gives_no_events() -> None:
    model = RandomFailureModel(seed=1, rate=0.0, skills={"s": FailureSkillConfig(weights={"STUCK": 1.0})})
    for i in range(50):
        assert model.next_event("s", i, {"missing": 3}) is None


def test_rate_one_always_gives_events() -> None:
    model = RandomFailureModel(seed=1, rate=1.0, skills={"s": FailureSkillConfig(weights={"STUCK": 1.0})})
    kinds = [model.next_event("s", i, {"missing": 3}) for i in range(50)]
    assert all(k is not None and k.kind == "STUCK" for k in kinds)


def test_skill_rate_override() -> None:
    skills = {"s": FailureSkillConfig(rate=1.0, weights={"STUCK": 1.0})}
    model = RandomFailureModel(seed=1, rate=0.0, skills=skills)
    assert model.next_event("s", 0, {"missing": 3}) is not None
    other = RandomFailureModel(seed=1, rate=0.0, skills={})
    assert other.next_event("s", 0, {"missing": 3}) is None


def test_weights_are_approximately_respected() -> None:
    """固定 seed 下的权重分布（大样本 + 容差，不 flaky）。"""
    skills = {"s": FailureSkillConfig(weights={"STUCK": 0.5, "INCOMPLETE": 0.5})}
    model = RandomFailureModel(seed=20240101, rate=1.0, skills=skills)
    kinds = [model.next_event("s", i, {"missing": 5}).kind for i in range(4000)]
    ratio = kinds.count("STUCK") / len(kinds)
    assert 0.47 < ratio < 0.53, f"权重分布偏离过大：{ratio}"


def test_random_model_is_reproducible() -> None:
    def run() -> list[str]:
        model = RandomFailureModel(seed=7, rate=0.5, skills={"s": FailureSkillConfig(weights={"STUCK": 1.0})})
        return [
            (model.next_event("s", i, {"missing": 3}) or FailureEvent("none")).kind
            for i in range(20)
        ]

    assert run() == run()


def test_skill_streams_are_independent() -> None:
    """一个技能的随机流被消费，不影响另一个技能。"""
    skills_a = {"a": FailureSkillConfig(weights={"STUCK": 1.0})}
    skills_b = {"b": FailureSkillConfig(weights={"STUCK": 1.0})}

    def run_b_first() -> list[bool]:
        model = RandomFailureModel(seed=11, rate=0.5, skills=skills_b)
        return [model.next_event("b", i, {"missing": 3}) is not None for i in range(20)]

    baseline = run_b_first()
    model = RandomFailureModel(seed=11, rate=0.5, skills={**skills_a, **skills_b})
    for i in range(100):
        model.next_event("a", i, {"missing": 3})
    after = [model.next_event("b", i, {"missing": 3}) is not None for i in range(20)]
    assert after == baseline


def test_timeout_lost_probability_follows_p_lost() -> None:
    skills = {"c": FailureSkillConfig(weights={"TIMEOUT": 1.0})}
    model = RandomFailureModel(seed=3, rate=1.0, skills=skills, p_lost=1.0)
    event = model.next_event("c", 0)
    assert event is not None and event.params["lost"] is True
    model2 = RandomFailureModel(seed=3, rate=1.0, skills=skills, p_lost=0.0)
    event2 = model2.next_event("c", 0)
    assert event2 is not None and event2.params["lost"] is False
