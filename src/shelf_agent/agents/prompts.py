"""ReAct agent 的 system prompt。

**英文**（多数开源/闭源模型对英文指令更稳），内容由契约自动生成——工具说明与
参数直接来自 YAML 契约，避免 prompt 与契约漂移。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ..contracts.types import EpisodeInfo

#: 默认暴露给 LLM 的工具集合：不含 ``robot1_get_head_image``
#: （图像直通 VLM 是后续工作，见 ``docs/assumptions.md`` A10）。
DEFAULT_EXPOSED_TOOLS: tuple[str, ...] = (
    "robot1_install_screws",
    "verify_box",
    "robot2_carry_box",
    "reset",
    "ask_human",
    "finish",
)

#: 四条硬规则（§3.7）。
RULES = (
    "Never call robot2_carry_box unless the most recent verify_box report was complete: "
    "clear is true, inside == {target}, and outside == 0.",
    "If a robot call returns timeout, that robot is stuck: call reset for that robot first, "
    "then retry the operation. A timeout tells you nothing about the box state.",
    "Call ask_human only when a robot cannot handle the problem (screws lying outside the box, "
    "or the box is missing from the station) or when your retry budget is exhausted.",
    "If the task cannot be completed, call finish with success=false and explain why. "
    "Never guess and never report success you did not observe.",
    "Reply with exactly one tool call per turn.",
)


def describe_tools(tools: Sequence[object], target: int) -> str:
    """把契约里的工具渲染成 prompt 里的说明段。"""
    lines: list[str] = []
    for tool in tools:
        params = ", ".join(
            f"{p.name}: {p.type}" + (f" (one of {list(p.values)})" if p.type == "enum" else "")
            for p in tool.params
        )
        returns = ", ".join(tool.returns_data) if tool.returns_data else "nothing"
        lines.append(f"- {tool.name}({params}) -> status, data: {returns}. {tool.description}")
    return "\n".join(lines)


def build_system_prompt(
    info: EpisodeInfo,
    tool_names: Iterable[str] | None = None,
) -> str:
    """生成 system prompt。"""
    wanted = set(tool_names) if tool_names is not None else None
    tools = [t for t in info.tools if wanted is None or t.name in wanted]
    rules = "\n".join(f"{index + 1}. {rule.format(target=info.target_screws)}" for index, rule in enumerate(RULES))
    return (
        "You are the controller of a two-robot screw-packing workstation.\n"
        f"The goal: fill one box with exactly {info.target_screws} screws and deliver the box "
        "to the shelf, then call finish with success=true.\n"
        "\n"
        "Tools you can call:\n"
        f"{describe_tools(tools, info.target_screws)}\n"
        "\n"
        "Rules:\n"
        f"{rules}\n"
        "\n"
        "A tool result tells you only what was observed: status, optional error_code, a message, "
        "the elapsed seconds and the declared data fields. The box state is never returned "
        "directly - use verify_box to observe it."
    )
