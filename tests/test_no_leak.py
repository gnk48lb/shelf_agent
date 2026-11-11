"""防泄漏测试：发给策略 / LLM 的内容里不得出现任何真值字段。

按 ``docs/assumptions.md`` A14：既查键名，也查取值。
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping, Sequence

import pytest

from shelf_agent.agents.rule_selfheal import RuleSelfHealPolicy
from shelf_agent.config import load_config
from shelf_agent.contracts.spec import build_spec_set
from shelf_agent.contracts.types import EpisodeInfo, ToolCall, ToolResult
from shelf_agent.eval.runner import run_episode
from shelf_agent.eval.scenarios import SCENARIOS, Scenario

CONFIG = load_config("configs/real_screw.yaml")

#: A14 登记的真值字段名（``inside`` / ``outside`` 属于验证**报告**，
#: 是契约声明的合法输出，因此按工具区分处理）。
TRUTH_ONLY_KEYS = frozenset(
    {
        "location",
        "robot_state",
        "robot_states",
        "fault",
        "truth",
        "truth_snapshot",
        "meta",
        "box_location",
        "missing",
        "boxes",
        "shelf_slots",
        "inside_count",
        "outside_count",
    }
)

#: 真值专有的取值 token（合法的错误码 BOX_NOT_AT_STATION 除外）。
TRUTH_ONLY_TOKENS = ("STATION", "SHELF", "LOST", "FAULT")

#: 契约声明过的 data 键——它们是「报告」而不是真值。
DECLARED_DATA_KEYS = frozenset(
    {
        "image_id",
        "path",
        "inside",
        "outside",
        "clear",
        "box_on_shelf",
        "answer",
    }
)

#: 允许出现真值 token 的错误码（它们本身就是给 agent 看的信号）。
ALLOWED_TOKEN_CODES = frozenset({"BOX_NOT_AT_STATION"})


class RecordingPolicy:
    """记录策略收到的全部输入，用来检查有没有泄漏。"""

    name = "rule"

    def __init__(self) -> None:
        self.info: EpisodeInfo | None = None
        self.histories: list[list[tuple[ToolCall, ToolResult]]] = []
        self._inner = RuleSelfHealPolicy()

    def start(self, info: EpisodeInfo) -> None:
        self.info = info
        self._inner.start(info)

    def next_call(self, history: Sequence[tuple[ToolCall, ToolResult]]) -> ToolCall:
        self.histories.append(list(history))
        return self._inner.next_call(history)


def _info_payload(info: EpisodeInfo) -> dict[str, Any]:
    return {
        "scenario": info.scenario,
        "target_screws": info.target_screws,
        "limits": dict(info.limits),
        "inspector_mode": info.inspector_mode,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "params": [p.name for p in tool.params],
                "returns_status": list(tool.returns_status),
                "returns_data": list(tool.returns_data),
                "preconditions": list(tool.preconditions),
                "effects": list(tool.effects),
            }
            for tool in info.tools
        ],
    }


def _result_payloads(policy: RecordingPolicy) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for history in policy.histories:
        for call, result in history:
            payloads.append({"call": call.to_dict(), "result": result.to_dict()})
    return payloads


def _walk(obj: Any, in_data: bool = False) -> Iterator[tuple[str, Any, bool]]:
    """遍历 (键, 值, 是否在 data 里)。"""
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield str(key), value, in_data
            yield from _walk(value, in_data or str(key) == "data")
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk(item, in_data)


def _run_scenario(name: str) -> RecordingPolicy:
    scenario: Scenario = SCENARIOS[name]
    recorder = RecordingPolicy()
    run_episode(
        CONFIG,
        seed=0,
        policy_name="rule",
        policy=recorder,
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )
    return recorder


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_policy_never_sees_truth_keys(name: str) -> None:
    """键名检查：真值字段不出现在策略输入里。"""
    recorder = _run_scenario(name)
    payload = _info_payload(recorder.info) if recorder.info else {}
    for entry in _result_payloads(recorder):
        for key, value, in_data in _walk(entry):
            if in_data:
                assert key in DECLARED_DATA_KEYS, f"出现了契约未声明的 data 键：{key}"
            else:
                assert key not in TRUTH_ONLY_KEYS, f"策略输入里出现真值字段 '{key}'"
    for key, _value, in_data in _walk(payload):
        assert not in_data
        assert key not in TRUTH_ONLY_KEYS, f"策略输入里出现真值字段 '{key}'"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_policy_never_sees_truth_values(name: str) -> None:
    """取值检查：真值 token 不出现在策略输入里（错误码除外）。"""
    recorder = _run_scenario(name)
    texts: list[str] = []
    if recorder.info is not None:
        for _key, value, in_data in _walk(_info_payload(recorder.info)):
            if not in_data and isinstance(value, str):
                texts.append(value)
    for entry in _result_payloads(recorder):
        result = entry["result"]
        if result.get("error_code") in ALLOWED_TOKEN_CODES:
            texts.append(result.get("message", ""))
        else:
            for _key, value, in_data in _walk(entry):
                if not in_data and isinstance(value, str):
                    texts.append(value)
    for text in texts:
        for token in TRUTH_ONLY_TOKENS:
            assert token not in text, f"策略输入里出现真值 token '{token}'：{text!r}"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_truth_snapshot_only_lives_in_the_trajectory(name: str) -> None:
    """真值快照只出现在轨迹里，不在 ToolResult 里。"""
    scenario = SCENARIOS[name]
    result = run_episode(
        CONFIG,
        seed=0,
        policy_name="rule",
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )
    for step in result.steps:
        assert "truth_snapshot" in step
        payload = step["result"]
        assert "truth_snapshot" not in payload
        assert set(payload) == {"status", "error_code", "message", "duration_s", "data"}


def test_contract_documents_do_not_leak_values() -> None:
    """契约里的 preconditions / effects 只是文档，不含真值 token。"""
    spec = build_spec_set(CONFIG)
    for tool in spec:
        for text in list(tool.preconditions) + list(tool.effects) + [tool.description]:
            for token in TRUTH_ONLY_TOKENS:
                assert token not in text, f"契约文本里出现真值 token '{token}'：{text!r}"
