"""假网关：按 messages 里的历史决定「LLM 会调哪个工具」。

它模拟的是**一个称职但会犯错的 LLM**：决策只依赖它已经看到的历史，工具结果
怎么回就怎么应对——跟真 LLM 的信息面完全一致（真值通道它一点都看不到）。

失误剧本（``--flaw``）用来展示「规则策略 vs LLM 策略」的差别：

* ``skip-verify`` —— 装完直接搬，不验证；
* ``no-reset`` —— 卡住了不 reset，直接再装一次；
* ``carry-first`` —— 一上来就搬（箱子还是空的）。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

#: 可选的「LLM 失误」剧本。
FLAWS = ("none", "skip-verify", "no-reset", "carry-first")


class FakeGateway:
    """本地假网关：返回 OpenAI 格式的 tool call 响应。"""

    def __init__(self, target: int, flaw: str = "none") -> None:
        self.target = target
        self.flaw = flaw
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(
        self,
        payload: Mapping[str, Any],
        url: str,
        headers: Mapping[str, str],
    ) -> Mapping[str, Any]:
        name, args = self.decide(payload.get("messages") or [])
        self.calls.append((name, args))
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call_{name}_{len(self.calls)}",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(args, ensure_ascii=False),
                                },
                            }
                        ],
                    }
                }
            ]
        }

    # ------------------------------------------------------------------ 决策

    def decide(self, messages: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
        """根据已有历史决定下一次调什么。"""
        pairs = pairs_from_messages(messages)
        state = summarize(pairs, self.target)

        # 0) 失误剧本：一上来就搬（箱子还是空的）——真机侧会记 unsafe_release。
        if self.flaw == "carry-first" and not state["carried"]:
            return ("robot2_carry_box", {})

        # 1) 搬运后复核报「箱子不在工位」→ 箱子已经在货架上 → 完工。
        if state["carried"] and state["last_verify_code"] == "BOX_NOT_AT_STATION":
            return ("finish", {"success": True, "summary": "箱子已搬到货架"})

        # 1b) 复核说箱子不在工位、但搬运从没成功过 → 箱子没了 → 求助人类。
        if state["last_verify_code"] == "BOX_NOT_AT_STATION" and not state["carried"]:
            return ("ask_human", {"question": "箱子不在工位，请把它放回工位"})

        # 2) 还没装完：install 卡住 → 先 reset 再重试（no-reset 剧本要白等几次）。
        if not state["installed"]:
            if state["robot1_faulted"]:
                if state["n_install"] >= 3 and not state["verified_once"]:
                    # 连着卡了三次 → 拍一张照，看看箱子到底还在不在工位
                    return ("verify_box", {})
                if self.flaw == "no-reset" and state["n_install"] < 3:
                    # 剧本：LLM 不信「卡住就得 reset」，直接再装一次
                    return ("robot1_install_screws", {})
                return ("reset", {"robot": "robot1"})
            return ("robot1_install_screws", {})

        # 3) 装完了：先验证（除非剧本说跳过）。
        if not state["verified_complete"] and self.flaw != "skip-verify":
            if state["outside"] > 0:
                return ("ask_human", {"question": "箱外有散落的螺丝，请放回箱内"})
            if state["verified_once"]:
                return ("robot1_install_screws", {})
            return ("verify_box", {})

        # 4) 验证通过（或者剧本让它跳过）→ 搬运。
        if not state["carried"]:
            return ("robot2_carry_box", {})

        # 5) 搬完了还没复核 → 复核（真机上这一步会看到「箱子不在工位」）。
        if not state["post_verified"]:
            return ("verify_box", {})

        return ("finish", {"success": True, "summary": "箱子已搬到货架"})


def pairs_from_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """从 messages 里还原 (工具名, 参数, 结果) 序列。"""
    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    pending: tuple[str, dict[str, Any]] | None = None
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            function = message["tool_calls"][0].get("function") or {}
            raw = function.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError:
                args = {}
            pending = (str(function.get("name")), args)
        elif role == "tool" and pending is not None:
            try:
                result = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                result = {}
            pairs.append((pending[0], pending[1], result))
            pending = None
    return pairs


def summarize(
    pairs: Sequence[tuple[str, dict[str, Any], dict[str, Any]]],
    target: int,
) -> dict[str, Any]:
    """从历史里推出 LLM 能看到的那些状态。"""
    state: dict[str, Any] = {
        "n_install": 0,
        "installed": False,
        "robot1_faulted": False,
        "verified_once": False,
        "verified_complete": False,
        "outside": 0,
        "inside": 0,
        "carried": False,
        "post_verified": False,
        "last_verify_code": None,
    }
    carry_seen = False
    for name, args, result in pairs:
        status = result.get("status")
        if name == "robot1_install_screws":
            state["n_install"] += 1
            if status == "done":
                state["installed"] = True
                state["robot1_faulted"] = False
                # 补装之后旧的验证报告就作废了，必须重新拍一张照。
                state["verified_once"] = False
                state["verified_complete"] = False
            elif status == "timeout":
                state["installed"] = False
                state["robot1_faulted"] = True
        elif name == "reset":
            if args.get("robot") in ("robot1", "all"):
                state["robot1_faulted"] = False
        elif name == "verify_box":
            if carry_seen:
                state["post_verified"] = True
            if status == "ok":
                data = result.get("data") or {}
                state["verified_once"] = True
                state["inside"] = int(data.get("inside", 0))
                state["outside"] = int(data.get("outside", 0))
                state["verified_complete"] = (
                    bool(data.get("clear"))
                    and state["inside"] == target
                    and state["outside"] == 0
                )
                state["last_verify_code"] = None
            else:
                state["last_verify_code"] = result.get("error_code")
        elif name == "robot2_carry_box":
            if status == "done":
                state["carried"] = True
                carry_seen = True
    return state
