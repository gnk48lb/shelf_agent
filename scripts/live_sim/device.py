"""打印器与设备层。

* :class:`Printer` —— 分层转录打印器（LLM 请求 / LLM 响应 / 设备往返 / 工具结果）。
* :class:`DeviceLink` —— 把一次技能调用翻译成实验室 API 的一次往返。
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping


def mask_headers(headers: Mapping[str, str]) -> str:
    """把 Authorization 脱敏后打印请求头。"""
    out = []
    for key, value in headers.items():
        if key.lower() == "authorization":
            token = str(value)
            value = f"{token[:7]}***{token[-4:]}" if len(token) > 12 else "***"
        out.append(f"{key}={value}")
    return ", ".join(out)


def indent(text: str, prefix: str = "  │ ") -> str:
    """给多行文本加前缀，便于在转录里缩进显示。"""
    return "\n".join(prefix + line for line in text.splitlines())


class Printer:
    """分层转录打印器。"""

    def __init__(self, show_system_prompt: bool = True) -> None:
        self.show_system_prompt = show_system_prompt
        self.n_requests = 0
        self.bytes_sent = 0
        self.retries = 0
        #: 下一次退避要打印的 HTTP 状态（transport 抛错前写进来）。
        self.pending_status: int | None = None

    @staticmethod
    def _rule(char: str = "─", width: int = 78) -> str:
        return char * width

    def header(self, title: str) -> None:
        print()
        print(self._rule("═"))
        print(f"  {title}")
        print(self._rule("═"))

    def turn(self, index: int) -> None:
        print()
        print(self._rule())
        print(f"  第 {index} 轮")
        print(self._rule())

    # ---------------------------------------------------------------- LLM 层

    def llm_request(
        self,
        payload: Mapping[str, Any],
        url: str,
        headers: Mapping[str, str],
    ) -> None:
        self.n_requests += 1
        body = json.dumps(payload, ensure_ascii=False)
        self.bytes_sent += len(body.encode("utf-8"))
        messages = payload.get("messages") or []
        tools = payload.get("tools") or []
        print(f"\n▶ LLM 请求 #{self.n_requests}  POST {url}")
        print(
            f"             model={payload.get('model')}  messages={len(messages)}  "
            f"tools={len(tools)}  temperature={payload.get('temperature')}"
        )
        print(f"             headers={mask_headers(headers)}")
        roles = " → ".join(str(m.get("role")) for m in messages)
        print(f"             roles: {roles}")
        if self.show_system_prompt and self.n_requests == 1:
            system = next((m for m in messages if m.get("role") == "system"), None)
            if system is not None:
                print("  ── system prompt（完整）" + "─" * 50)
                for line in str(system.get("content", "")).splitlines():
                    print(f"  │ {line}")
                print("  " + "─" * 78)
            print("  ── tools schema（第一个工具的完整定义）" + "─" * 37)
            if tools:
                print(indent(json.dumps(tools[0], ensure_ascii=False, indent=2)))
            print("  " + "─" * 78)

    def llm_response(self, body: Mapping[str, Any]) -> None:
        choices = body.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        calls = message.get("tool_calls") or []
        if not calls:
            print(f"\n◀ LLM 响应   （无 tool call）content={str(message.get('content'))[:80]!r}")
            return
        call = calls[0]
        function = call.get("function") or {}
        print(
            f"\n◀ LLM 响应   tool_call  {function.get('name')}"
            f"({function.get('arguments')})   id={call.get('id')}"
        )

    def llm_retry(self, delay_s: float) -> None:
        self.retries += 1
        status = self.pending_status
        self.pending_status = None
        print(f"\n⚠ LLM 请求失败（status={status}）→ 退避 {delay_s:.2f}s 后重试")

    # -------------------------------------------------------------- 设备层

    def device_call(self, tool: str, lab_name: str, robot: str | None) -> None:
        print(f"\n▶ 设备往返  {lab_name}   (工具 {tool}, robot={robot or '—'})")

    def device_result(self, result: Mapping[str, Any]) -> None:
        print(
            f"◀ 设备返回  status={result.get('status')}  "
            f"duration_s={result.get('duration_s')}"
        )

    def tool_result(self, result: Mapping[str, Any]) -> None:
        print(f"▶ 工具结果  {json.dumps(result, ensure_ascii=False, sort_keys=True)}")


class DeviceLink:
    """把一次技能调用翻译成实验室 API 的一次往返。

    真机上这一层是实验室的 SDK / HTTP 网关：``lab_name`` 就是实验室里的调用名，
    ``timeout_s`` 是它的硬超时。``speed > 0`` 时按契约耗时真睡墙钟——这就是
    「虚拟时钟」在真机上被「真实时间」取代的地方。
    """

    def __init__(
        self,
        inner: Any,
        spec: Any,
        speed: float = 0.0,
        printer: Printer | None = None,
    ) -> None:
        self.inner = inner
        self.spec = spec
        self.speed = speed
        self.printer = printer
        self.hops: list[dict[str, Any]] = []
        self.wall_s = 0.0

    def invoke(self, tool: str, args: Mapping[str, Any]) -> Any:
        tool_spec = self.spec.get(tool)
        lab_name = tool_spec.lab_name or tool
        if self.printer is not None:
            self.printer.device_call(tool, lab_name, tool_spec.robot)
        started = time.perf_counter()
        if self.speed > 0:
            time.sleep(self.blocking_seconds(tool_spec) * self.speed)
        result = self.inner.invoke(tool, args)
        elapsed = time.perf_counter() - started
        self.wall_s += elapsed
        self.hops.append(
            {
                "lab_name": lab_name,
                "tool": tool,
                "wall_s": round(elapsed, 3),
                "status": result.status,
                "duration_s": result.duration_s,
            }
        )
        if self.printer is not None:
            self.printer.device_result(result.to_dict())
        return result

    @staticmethod
    def blocking_seconds(tool_spec: Any) -> float:
        """这次调用在真机上要占多久。

        阻塞型物理技能（装螺丝 / 搬运）按硬超时算——卡住时它会一直占着；
        其余工具按契约里的平均耗时算。
        """
        if tool_spec.reset_durations:
            return float(max(tool_spec.reset_durations.values()))
        if tool_spec.robot in ("robot1", "robot2") and tool_spec.timeout_s:
            return float(tool_spec.timeout_s)
        return float(tool_spec.mean_s)

    def debug_truth(self) -> Mapping[str, Any]:
        return self.inner.debug_truth()
