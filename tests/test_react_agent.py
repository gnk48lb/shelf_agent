"""ReAct agent 单测：完整 episode、异常分支、多 tool call、messages 格式。"""

from __future__ import annotations

import json

import pytest

from shelf_agent.agents.llm_client import LLMResponse, ScriptedLLM, ToolCallRequest
from shelf_agent.agents.react_agent import AgentError, ReActAgent
from shelf_agent.config import load_config
from shelf_agent.contracts.types import EpisodeInfo
from shelf_agent.eval.runner import run_react_episode
from shelf_agent.eval.scenarios import SCENARIOS

CONFIG = load_config("configs/real_screw.yaml")


def _agent(responses) -> ReActAgent:
    return ReActAgent(client=ScriptedLLM(responses))


def _call(name: str, arguments: dict | None = None, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCallRequest(name, arguments or {}, call_id)],
    )


def test_react_agent_runs_a_full_episode() -> None:
    """脚本化 LLM 走通 happy 场景。"""
    agent = _agent(
        [
            _call("robot1_install_screws", {}, "c1"),
            _call("verify_box", {}, "c2"),
            _call("robot2_carry_box", {}, "c3"),
            _call("finish", {"success": True, "summary": "done"}, "c4"),
        ]
    )
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert result.outcome == "SUCCESS"
    names = [step["call"]["name"] for step in result.steps]
    assert names == ["robot1_install_screws", "verify_box", "robot2_carry_box", "finish"]


def test_react_agent_handles_unknown_tool() -> None:
    """LLM 调了不存在的工具 → executor 返回 error，agent 能继续。"""
    agent = _agent(
        [
            _call("teleport_box", {}, "c1"),
            _call("robot1_install_screws", {}, "c2"),
            _call("finish", {"success": False, "summary": "give up"}, "c3"),
        ]
    )
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    first = result.steps[0]
    assert first["result"]["status"] == "error"
    assert first["result"]["error_code"] == "UNKNOWN_TOOL"
    assert result.outcome == "SAFE_ABORT"


def test_react_agent_handles_invalid_arguments() -> None:
    """参数类型不对 → INVALID_ARGS，不推进时钟。"""
    agent = _agent(
        [
            _call("reset", {"robot": "robot9"}, "c1"),
            _call("finish", {"success": False, "summary": "x"}, "c2"),
        ]
    )
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert result.steps[0]["result"]["error_code"] == "INVALID_ARGS"
    assert result.steps[0]["t_s"] == 0.0


def test_react_agent_without_tool_call_retries_then_raises() -> None:
    """连续两次没有 tool call → AgentError → AGENT_ERROR。"""
    agent = _agent([LLMResponse(content="我想想", tool_calls=[])])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert result.outcome == "AGENT_ERROR"
    assert result.steps == []


def test_react_agent_single_no_tool_call_then_ok() -> None:
    """只有一次没有 tool call → 追加提醒后重试即可继续。"""
    agent = _agent(
        [
            LLMResponse(content="hmm", tool_calls=[]),
            _call("finish", {"success": False, "summary": "stop"}, "c2"),
        ]
    )
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert result.outcome == "SAFE_ABORT"
    assert len(result.steps) == 1


def test_react_agent_takes_only_the_first_tool_call() -> None:
    """一次给了多个 tool call → 只执行第一个。"""
    response = LLMResponse(
        content="",
        tool_calls=[
            ToolCallRequest("robot1_install_screws", {}, "c1"),
            ToolCallRequest("robot2_carry_box", {}, "c2"),
        ],
    )
    agent = _agent([response, _call("finish", {"success": False, "summary": "x"}, "c3")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert [step["call"]["name"] for step in result.steps] == ["robot1_install_screws", "finish"]


def test_react_agent_messages_are_openai_chat_format() -> None:
    agent = _agent(
        [
            _call("robot1_install_screws", {}, "c1"),
            _call("finish", {"success": False, "summary": "x"}, "c2"),
        ]
    )
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert result.messages is not None
    roles = [m["role"] for m in result.messages]
    assert roles[0] == "system"
    assert "assistant" in roles
    assert "tool" in roles
    system = result.messages[0]
    assert "verify_box" in system["content"]
    for message in result.messages:
        if message["role"] == "tool":
            assert "tool_call_id" in message
            payload = json.loads(message["content"])
            assert set(payload) == {"status", "error_code", "message", "duration_s", "data"}
        if message["role"] == "assistant":
            assert message["tool_calls"][0]["type"] == "function"


def test_react_agent_tools_schema_is_recorded() -> None:
    agent = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    assert result.tools_schema is not None
    names = [t["function"]["name"] for t in result.tools_schema]
    assert "verify_box" in names
    assert "robot1_get_head_image" not in names, "默认不暴露头部相机工具"
    assert "robot2_carry_box" in names


def test_react_agent_system_prompt_states_the_rules() -> None:
    agent = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    prompt = result.messages[0]["content"]  # type: ignore[index]
    assert "robot2_carry_box" in prompt
    assert "verify_box" in prompt
    assert f"== {CONFIG.target_screws}" in prompt
    assert "ask_human" in prompt
    assert "success=false" in prompt.lower()


def test_react_agent_exposes_head_image_on_request() -> None:
    agent = ReActAgent(
        client=ScriptedLLM([_call("finish", {"success": False, "summary": "x"}, "c1")]),
        expose_head_image=True,
    )
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    names = [t["function"]["name"] for t in result.tools_schema or []]
    assert "robot1_get_head_image" in names


def _has_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def test_system_prompt_is_english_only() -> None:
    """LLM 看到的文本里不能混中文（工具描述用 description_en）。"""
    agent = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    prompt = result.messages[0]["content"]  # type: ignore[index]
    assert not _has_chinese(prompt), "系统提示里出现了中文"


def test_tools_schema_is_english_only() -> None:
    agent = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    for entry in result.tools_schema or []:
        assert not _has_chinese(entry["function"]["description"])
        for param in entry["function"]["parameters"]["properties"].values():
            assert not _has_chinese(param.get("description", ""))


def test_lab_name_is_not_exposed_to_the_llm() -> None:
    """实验室名只用于人读的映射，不进 LLM 的 schema。"""
    agent = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    for entry in result.tools_schema or []:
        assert "robot1.install_screws" not in entry["function"]["description"]


def test_inspector_is_exposed_only_in_shelf_status_mode() -> None:
    """inspector.mode = shelf_status 时 ReAct 才能看到 inspector_run。"""
    agent = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result = run_react_episode(CONFIG, seed=0, agent=agent, failure_rate=0.0)
    names = [t["function"]["name"] for t in result.tools_schema or []]
    assert "inspector_run" not in names

    from shelf_agent.config import build_config

    raw = {
        **CONFIG.raw,
        "inspector": {**CONFIG.raw["inspector"], "mode": "shelf_status"},
    }
    config = build_config(raw)
    agent2 = _agent([_call("finish", {"success": False, "summary": "x"}, "c1")])
    result2 = run_react_episode(config, seed=0, agent=agent2, failure_rate=0.0)
    names2 = [t["function"]["name"] for t in result2.tools_schema or []]
    assert "inspector_run" in names2
    assert "inspection robot" in result2.messages[0]["content"].lower()  # type: ignore[index]


def test_agent_error_exception_is_raised_by_the_agent() -> None:
    agent = _agent([LLMResponse(content="x", tool_calls=[])])
    agent.start(
        EpisodeInfo(scenario="real_screw", target_screws=6, tools=(), limits={})
    )
    with pytest.raises(AgentError):
        agent.next_call([])


def test_react_agent_on_a_scripted_failure_scenario() -> None:
    """脚本化故障 + ReAct：先装 → 验证不满 → 再装 → 验证 → 搬 → 收工。"""
    agent = _agent(
        [
            _call("robot1_install_screws", {}, "c1"),
            _call("verify_box", {}, "c2"),
            _call("robot1_install_screws", {}, "c3"),
            _call("verify_box", {}, "c4"),
            _call("robot2_carry_box", {}, "c5"),
            _call("finish", {"success": True, "summary": "ok"}, "c6"),
        ]
    )
    result = run_react_episode(
        CONFIG,
        seed=0,
        agent=agent,
        failure_script=SCENARIOS["incomplete_topup"].failure_script,
        failure_rate=0.0,
    )
    assert result.outcome == "SUCCESS"
