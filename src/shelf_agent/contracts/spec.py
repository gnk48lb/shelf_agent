"""工具契约的加载与校验。

契约来自 YAML（``configs/*.yaml`` 的 ``tools`` 段）。本模块只做**结构**校验：
字段是否齐全、类型是否合法、工具名是否合规。``preconditions`` / ``effects``
只是给人看的文档，语义实现在 ``env/sim_backend.py`` 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from ..config import AppConfig, Duration

#: 工具名允许的字符（OpenAI 兼容 API 不允许点号）。
TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

#: 允许的参数类型。
PARAM_TYPES = ("string", "int", "bool", "enum")

#: 每个工具必须提供的字段。
REQUIRED_TOOL_FIELDS = (
    "name",
    "description",
    "params",
    "returns_status",
    "returns_data",
    "timeout_s",
    "duration",
)


class ContractError(ValueError):
    """契约 YAML 不合法。错误信息必须指出具体字段路径。"""


@dataclass(frozen=True)
class ParamSpec:
    """一个工具参数的规格。"""

    name: str
    type: str
    required: bool = True
    values: tuple[str, ...] = ()
    description: str = ""

    def to_json_schema(self) -> dict[str, Any]:
        """转成 JSON Schema 片段。"""
        if self.type == "enum":
            return {"type": "string", "enum": list(self.values)}
        mapping = {"string": "string", "int": "integer", "bool": "boolean"}
        return {"type": mapping[self.type]}


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整契约。"""

    name: str
    description: str
    params: tuple[ParamSpec, ...] = ()
    returns_status: tuple[str, ...] = ()
    returns_data: tuple[str, ...] = ()
    timeout_s: float = 0.0
    mean_s: float = 0.0
    jitter: float = 0.0
    lab_name: str | None = None
    robot: str | None = None
    preconditions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    reset_durations: Mapping[str, float] = field(default_factory=dict)
    description_en: str = ""

    @property
    def duration(self) -> Duration:
        """耗时的均值 + 抖动模型。"""
        return Duration(mean_s=self.mean_s, jitter=self.jitter)

    @property
    def english_description(self) -> str:
        """给 LLM 用的英文描述（缺省回退中文 description）。"""
        return self.description_en or self.description

    def required_params(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.required)


@dataclass(frozen=True)
class ToolSpecSet:
    """一组工具契约。"""

    scenario: str
    target_screws: int
    tools: Mapping[str, ToolSpec] = field(default_factory=dict)

    def get(self, name: str) -> ToolSpec:
        """按名字取契约；不存在时抛 ContractError。"""
        try:
            return self.tools[name]
        except KeyError:
            raise ContractError(f"未知工具 '{name}'，可用工具：{sorted(self.tools)}") from None

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(self.tools.values())

    def __len__(self) -> int:
        return len(self.tools)


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} 必须是映射（dict），实际是 {type(value).__name__}")
    return value


def _require_str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{path} 必须是字符串，实际是 {type(value).__name__}")
    return value


def _require_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} 必须是数值，实际是 {type(value).__name__}")
    return float(value)


def _require_str_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"{path} 必须是列表，实际是 {type(value).__name__}")
    out = []
    for i, item in enumerate(value):
        out.append(_require_str(item, f"{path}[{i}]"))
    return tuple(out)


def _parse_params(raw: Any, path: str) -> tuple[ParamSpec, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ContractError(f"{path} 必须是列表，实际是 {type(raw).__name__}")
    params: list[ParamSpec] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        item_path = f"{path}[{i}]"
        mapping = _require_mapping(item, item_path)
        if "name" not in mapping:
            raise ContractError(f"{item_path} 缺少必填字段 'name'")
        name = _require_str(mapping["name"], f"{item_path}.name")
        if name in seen:
            raise ContractError(f"{item_path}.name 重复的参数名 '{name}'")
        seen.add(name)
        ptype = mapping.get("type")
        if ptype is None:
            raise ContractError(f"{item_path} 缺少必填字段 'type'")
        ptype = _require_str(ptype, f"{item_path}.type")
        if ptype not in PARAM_TYPES:
            raise ContractError(
                f"{item_path}.type 必须是 {list(PARAM_TYPES)} 之一，实际是 '{ptype}'"
            )
        values: tuple[str, ...] = ()
        if ptype == "enum":
            if "values" not in mapping:
                raise ContractError(f"{item_path} 缺少必填字段 'values'（enum 类型必须有）")
            values = _require_str_list(mapping["values"], f"{item_path}.values")
            if not values:
                raise ContractError(f"{item_path}.values 不能为空")
        required = mapping.get("required", True)
        if not isinstance(required, bool):
            raise ContractError(f"{item_path}.required 必须是布尔值")
        description = mapping.get("description", "")
        description = _require_str(description, f"{item_path}.description")
        params.append(
            ParamSpec(name=name, type=ptype, required=required, values=values, description=description)
        )
    return tuple(params)


def _parse_tool(raw: Any, index: int) -> ToolSpec:
    path = f"tools[{index}]"
    mapping = _require_mapping(raw, path)
    for key in REQUIRED_TOOL_FIELDS:
        if key not in mapping:
            raise ContractError(f"{path} 缺少必填字段 '{key}'")
    name = _require_str(mapping["name"], f"{path}.name")
    if not TOOL_NAME_RE.match(name):
        raise ContractError(
            f"{path}.name '{name}' 不合规，只允许字母、数字、下划线和连字符"
        )
    duration = _require_mapping(mapping["duration"], f"{path}.duration")
    if "mean_s" not in duration:
        raise ContractError(f"{path}.duration 缺少必填字段 'mean_s'")
    mean_s = _require_number(duration["mean_s"], f"{path}.duration.mean_s")
    jitter = _require_number(duration.get("jitter", 0.0), f"{path}.duration.jitter")
    if jitter < 0:
        raise ContractError(f"{path}.duration.jitter 不能为负")
    lab_name = mapping.get("lab_name")
    if lab_name is not None:
        lab_name = _require_str(lab_name, f"{path}.lab_name")
    robot = mapping.get("robot")
    if robot is not None:
        robot = _require_str(robot, f"{path}.robot")
    reset_durations_raw = mapping.get("reset_durations", {}) or {}
    reset_durations = _require_mapping(reset_durations_raw, f"{path}.reset_durations")
    reset_durations = {
        str(k): _require_number(v, f"{path}.reset_durations.{k}") for k, v in reset_durations.items()
    }
    return ToolSpec(
        name=name,
        description=_require_str(mapping["description"], f"{path}.description"),
        description_en=_require_str(mapping.get("description_en", ""), f"{path}.description_en"),
        params=_parse_params(mapping["params"], f"{path}.params"),
        returns_status=_require_str_list(mapping["returns_status"], f"{path}.returns_status"),
        returns_data=_require_str_list(mapping["returns_data"], f"{path}.returns_data"),
        timeout_s=_require_number(mapping["timeout_s"], f"{path}.timeout_s"),
        mean_s=mean_s,
        jitter=jitter,
        lab_name=lab_name,
        robot=robot,
        preconditions=_require_str_list(mapping.get("preconditions", []), f"{path}.preconditions"),
        effects=_require_str_list(mapping.get("effects", []), f"{path}.effects"),
        reset_durations=reset_durations,
    )


def build_spec_set(config: AppConfig) -> ToolSpecSet:
    """从 AppConfig 构造并校验工具契约集合。"""
    raw_tools = config.raw.get("tools")
    if not isinstance(raw_tools, (list, tuple)) or not raw_tools:
        raise ContractError("配置段 'tools' 必须是非空列表")
    tools: dict[str, ToolSpec] = {}
    for index, raw in enumerate(raw_tools):
        spec = _parse_tool(raw, index)
        if spec.name in tools:
            raise ContractError(f"tools[{index}].name '{spec.name}' 与前面的工具重名")
        tools[spec.name] = spec
    return ToolSpecSet(
        scenario=config.scenario,
        target_screws=config.target_screws,
        tools=tools,
    )


def load_spec_set(config: AppConfig) -> ToolSpecSet:
    """build_spec_set 的别名，语义更贴近调用方。"""
    return build_spec_set(config)


def spec_signature(spec: ToolSpecSet) -> str:
    """契约的稳定摘要，用于 config_hash 之外的调试。"""
    names: Sequence[str] = sorted(spec.tools)
    return "|".join(names)
