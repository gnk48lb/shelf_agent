"""契约层单测：合法加载、各类非法 YAML 的错误定位、OpenAI schema 结构。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from shelf_agent.config import build_config, load_config
from shelf_agent.contracts.schema import (
    ArgsError,
    to_openai_tools,
    validate_args,
    validate_result_data,
)
from shelf_agent.contracts.spec import ContractError, build_spec_set
from shelf_agent.contracts.types import ContractViolation

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "real_screw.yaml"


def _raw() -> dict:
    return copy.deepcopy(load_config(CONFIG_PATH).raw)


def _spec():
    return build_spec_set(build_config(_raw()))


def test_load_real_config() -> None:
    config = load_config(CONFIG_PATH)
    spec = build_spec_set(config)
    assert spec.scenario == "real_screw"
    assert spec.target_screws == 6
    assert len(spec) == 8
    assert "robot1_install_screws" in spec
    install = spec.get("robot1_install_screws")
    assert install.lab_name == "robot1.install_screws"
    assert install.timeout_s == 120
    assert install.duration.mean_s == 45
    assert install.duration.jitter == 0.2
    assert install.returns_status == ("done", "timeout")
    assert install.returns_data == ()


def test_tool_names_are_valid() -> None:
    """工具名必须符合 ^[a-zA-Z0-9_-]+$（OpenAI 兼容 API 不允许点号）。"""
    spec = _spec()
    for tool in spec:
        assert tool.name.replace("_", "").replace("-", "").isalnum()
        assert "." not in tool.name


def test_duplicate_tool_name_rejected() -> None:
    raw = _raw()
    raw["tools"].append(copy.deepcopy(raw["tools"][0]))
    with pytest.raises(ContractError, match=r"tools\[8\]\.name .*重名"):
        build_spec_set(build_config(raw))


def test_missing_required_field_points_at_path() -> None:
    raw = _raw()
    del raw["tools"][0]["timeout_s"]
    with pytest.raises(ContractError, match=r"tools\[0\] 缺少必填字段 'timeout_s'"):
        build_spec_set(build_config(raw))


def test_invalid_tool_name_points_at_path() -> None:
    raw = _raw()
    raw["tools"][0]["name"] = "robot1.install"
    with pytest.raises(ContractError, match=r"tools\[0\]\.name 'robot1\.install' 不合规"):
        build_spec_set(build_config(raw))


def test_invalid_param_type_points_at_path() -> None:
    raw = _raw()
    raw["tools"][0]["params"] = [{"name": "box_id", "type": "float"}]
    with pytest.raises(ContractError, match=r"tools\[0\]\.params\[0\]\.type"):
        build_spec_set(build_config(raw))


def test_enum_param_requires_values() -> None:
    raw = _raw()
    raw["tools"][6]["params"] = [{"name": "robot", "type": "enum"}]
    with pytest.raises(ContractError, match=r"tools\[6\]\.params\[0\] 缺少必填字段 'values'"):
        build_spec_set(build_config(raw))


def test_duration_missing_mean_points_at_path() -> None:
    raw = _raw()
    raw["tools"][0]["duration"] = {"jitter": 0.2}
    with pytest.raises(ContractError, match=r"tools\[0\]\.duration 缺少必填字段 'mean_s'"):
        build_spec_set(build_config(raw))


def test_returns_data_must_be_list() -> None:
    raw = _raw()
    raw["tools"][2]["returns_data"] = "inside"
    with pytest.raises(ContractError, match=r"tools\[2\]\.returns_data 必须是列表"):
        build_spec_set(build_config(raw))


def test_unknown_tool_raises_with_available_names() -> None:
    spec = _spec()
    with pytest.raises(ContractError, match="未知工具 'nope'"):
        spec.get("nope")


def test_validate_args_ok() -> None:
    spec = _spec()
    reset = spec.get("reset")
    assert validate_args(reset, {"robot": "robot2"}) == {"robot": "robot2"}
    finish = spec.get("finish")
    assert validate_args(finish, {"success": True, "summary": "好了"}) == {
        "success": True,
        "summary": "好了",
    }


def test_validate_args_rejects_unknown_param() -> None:
    spec = _spec()
    with pytest.raises(ArgsError, match="不接受参数 'extra'"):
        validate_args(spec.get("reset"), {"robot": "robot1", "extra": 1})


def test_validate_args_rejects_missing_required() -> None:
    spec = _spec()
    with pytest.raises(ArgsError, match="缺少必填参数 'robot'"):
        validate_args(spec.get("reset"), {})


def test_validate_args_rejects_bad_type() -> None:
    spec = _spec()
    with pytest.raises(ArgsError, match="参数 'success' 应为 bool"):
        validate_args(spec.get("finish"), {"success": "yes", "summary": "x"})


def test_validate_args_rejects_bool_as_int() -> None:
    spec = _spec()
    raw = _raw()
    raw["tools"][0]["params"] = [{"name": "n", "type": "int"}]
    spec2 = build_spec_set(build_config(raw))
    with pytest.raises(ArgsError, match="参数 'n' 应为 int"):
        validate_args(spec2.get("robot1_install_screws"), {"n": True})


def test_validate_args_rejects_bad_enum() -> None:
    spec = _spec()
    with pytest.raises(ArgsError, match="参数 'robot' 应为 enum"):
        validate_args(spec.get("reset"), {"robot": "robot3"})


def test_validate_result_data_rejects_undeclared_key() -> None:
    spec = _spec()
    validate_result_data(spec.get("verify_box"), {"inside": 6, "outside": 0, "clear": True})
    with pytest.raises(ContractViolation, match="未声明的 data 键"):
        validate_result_data(spec.get("verify_box"), {"inside": 6, "location": "SHELF"})


def test_to_openai_tools_structure() -> None:
    tools = to_openai_tools(_spec())
    assert isinstance(tools, list)
    names = [t["function"]["name"] for t in tools]
    assert "verify_box" in names
    for entry in tools:
        assert entry["type"] == "function"
        func = entry["function"]
        assert isinstance(func["description"], str) and func["description"]
        params = func["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        assert isinstance(params["required"], list)


def test_to_openai_tools_enum_schema() -> None:
    tools = {t["function"]["name"]: t for t in to_openai_tools(_spec())}
    reset = tools["reset"]["function"]["parameters"]
    assert reset["properties"]["robot"]["type"] == "string"
    assert reset["properties"]["robot"]["enum"] == ["robot1", "robot2", "all"]
    assert reset["required"] == ["robot"]


def test_to_openai_tools_no_params_tool() -> None:
    tools = {t["function"]["name"]: t for t in to_openai_tools(_spec())}
    verify = tools["verify_box"]["function"]["parameters"]
    assert verify["properties"] == {}
    assert verify["required"] == []


def test_contract_yaml_is_valid_yaml() -> None:
    """契约文件本身能被 yaml 解析且顶层段齐全。"""
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for key in (
        "scenario", "target_screws", "tools", "failures", "verifier",
        "human", "inspector", "budget", "rule_policy",
    ):
        assert key in data, f"配置缺少顶层段 '{key}'"
