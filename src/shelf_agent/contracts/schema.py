"""参数校验与 OpenAI function-calling schema 生成。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .spec import ParamSpec, ToolSpec, ToolSpecSet
from .types import ContractViolation


class ArgsError(ValueError):
    """调用参数不合法。错误信息指出具体参数名与期望类型。"""


def _type_name(spec: ParamSpec) -> str:
    if spec.type == "enum":
        return f"enum{list(spec.values)}"
    return spec.type


def validate_args(spec: ToolSpec, args: Mapping[str, Any] | None) -> dict[str, Any]:
    """按契约校验并归一化参数。

    校验失败时抛 :class:`ArgsError`，信息里带具体参数名。返回值是归一化后的
    参数字典（多余参数会被拒绝，不做隐式转换）。
    """
    given = dict(args or {})
    known = {p.name: p for p in spec.params}
    for key in given:
        if key not in known:
            raise ArgsError(
                f"工具 '{spec.name}' 不接受参数 '{key}'，可用参数：{sorted(known) or '（无）'}"
            )
    out: dict[str, Any] = {}
    for param in spec.params:
        if param.name not in given:
            if param.required:
                raise ArgsError(f"工具 '{spec.name}' 缺少必填参数 '{param.name}'")
            continue
        value = given[param.name]
        if param.type == "string":
            if not isinstance(value, str):
                raise ArgsError(
                    f"参数 '{param.name}' 应为 string，实际是 {type(value).__name__}"
                )
        elif param.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ArgsError(
                    f"参数 '{param.name}' 应为 int，实际是 {type(value).__name__}"
                )
        elif param.type == "bool":
            if not isinstance(value, bool):
                raise ArgsError(
                    f"参数 '{param.name}' 应为 bool，实际是 {type(value).__name__}"
                )
        elif param.type == "enum":
            if not isinstance(value, str) or value not in param.values:
                raise ArgsError(
                    f"参数 '{param.name}' 应为 enum{list(param.values)} 之一，实际是 {value!r}"
                )
        out[param.name] = value
    return out


def validate_result_data(spec: ToolSpec, data: Mapping[str, Any]) -> None:
    """检查返回的 data 键都在契约 ``returns_data`` 里声明过。

    多出未声明的键视为 bug，抛 :class:`ContractViolation`。
    """
    declared = set(spec.returns_data)
    extra = sorted(set(data) - declared)
    if extra:
        raise ContractViolation(
            f"工具 '{spec.name}' 返回了未声明的 data 键：{extra}；契约声明的是 {sorted(declared)}"
        )


def _param_json_schema(param: ParamSpec) -> dict[str, Any]:
    schema = param.to_json_schema()
    if param.description:
        schema = {**schema, "description": param.description}
    return schema


def tool_to_openai_schema(spec: ToolSpec) -> dict[str, Any]:
    """单个工具转成 OpenAI function-calling 的 function 定义。"""
    description = spec.description
    if spec.lab_name:
        description = f"{description}（实验室名：{spec.lab_name}）"
    properties = {p.name: _param_json_schema(p) for p in spec.params}
    required = [p.name for p in spec.params if p.required]
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def to_openai_tools(spec: ToolSpecSet | Iterable[ToolSpec]) -> list[dict[str, Any]]:
    """把契约集合转成 ``messages`` 里 ``tools`` 参数用的 schema 列表。"""
    items = list(spec) if isinstance(spec, ToolSpecSet) else list(spec)
    return [tool_to_openai_schema(item) for item in items]
