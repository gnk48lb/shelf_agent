"""技能后端接口。

``SimBackend``（仿真）与将来的真机实现共用同一套签名——换后端时 agent 一行不改
（真机接入不在本期范围，见 ``docs/open_questions.md`` Q10）。
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ..contracts.types import ToolResult


class SkillBackend(Protocol):
    """执行技能的后端。"""

    def invoke(self, tool: str, args: Mapping[str, Any]) -> ToolResult:
        """执行一次技能调用。未知工具应返回 error 而不是抛异常。"""

    def debug_truth(self) -> Mapping[str, Any]:
        """真值快照——仅供评测与测试，agent 侧不得调用。"""
