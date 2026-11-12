"""真机 + LLM 调用流程模拟的实现包（入口见 ``scripts/live_flow_sim.py``）。

* :mod:`live_sim.gateway` —— 假网关：按 messages 里的历史决定 LLM 会调哪个工具。
* :mod:`live_sim.device` —— 打印器与设备层（把技能调用翻译成实验室 API 往返）。
"""

from .device import DeviceLink, Printer
from .gateway import FakeGateway

__all__ = ["DeviceLink", "FakeGateway", "Printer"]
