"""仿真后端：把 §3.2 的语义规则逐条实现。

关键约定：

* 耗时记在虚拟时钟上，**成功的调用**耗时是抽样值，**timeout** 的耗时正好是
  契约里的 ``timeout_s``；
* 前置不满足（含机器人处于 FAULT）表现为「卡住但不改变状态」；
* install / carry 只返回 status 与耗时，不带任何状态信息。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..config import AppConfig
from ..contracts.spec import ToolSpec, ToolSpecSet
from ..contracts.types import (
    ERR_BOX_NOT_AT_STATION,
    ERR_UNKNOWN_TOOL,
    BoxLocation,
    RobotState,
    ToolResult,
)
from ..perception.imagelib import ImageLibrary
from ..perception.verifier import Verifier
from ..utils.clock import VirtualClock
from ..utils.rng import derive_rng
from .failures import FailureModel
from .human import ScriptedHuman
from .world import World

#: 工具名常量，避免到处写字符串。
TOOL_INSTALL = "robot1_install_screws"
TOOL_HEAD_IMAGE = "robot1_get_head_image"
TOOL_VERIFY = "verify_box"
TOOL_CARRY = "robot2_carry_box"
TOOL_RESET = "reset"
TOOL_INSPECTOR = "inspector_run"
TOOL_ASK_HUMAN = "ask_human"
TOOL_FINISH = "finish"

#: 箱子不在工位时的错误耗时（秒）。
NOT_AT_STATION_DURATION_S = 1.0


@dataclass
class BackendMetrics:
    """后端在仿真过程中记录的指标（供评测使用）。"""

    n_timeouts: int = 0
    n_resets: int = 0
    n_unnecessary_resets: int = 0
    n_wasted_calls: int = 0
    unsafe_release: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_timeouts": self.n_timeouts,
            "n_resets": self.n_resets,
            "n_unnecessary_resets": self.n_unnecessary_resets,
            "n_wasted_calls": self.n_wasted_calls,
            "unsafe_release": self.unsafe_release,
        }


class SimBackend:
    """按契约执行技能的仿真后端。"""

    def __init__(
        self,
        config: AppConfig,
        spec: ToolSpecSet,
        world: World,
        failures: FailureModel,
        images: ImageLibrary,
        verifier: Verifier,
        human: ScriptedHuman,
        clock: VirtualClock,
        seed: int = 0,
        box_id: str = "box_1",
    ) -> None:
        self.config = config
        self.spec = spec
        self.world = world
        self.failures = failures
        self.images = images
        self.verifier = verifier
        self.human = human
        self.clock = clock
        self.seed = seed
        self.box_id = box_id
        self.target = config.target_screws
        self.metrics = BackendMetrics()
        self._call_counts: dict[str, int] = {}
        self._rngs: dict[str, random.Random] = {}
        self._handlers: dict[str, Callable[[Mapping[str, Any]], ToolResult]] = {
            TOOL_INSTALL: self._install,
            TOOL_HEAD_IMAGE: self._head_image,
            TOOL_VERIFY: self._verify,
            TOOL_CARRY: self._carry,
            TOOL_RESET: self._reset,
            TOOL_INSPECTOR: self._inspector,
            TOOL_ASK_HUMAN: self._ask_human,
            TOOL_FINISH: self._finish,
        }

    # ------------------------------------------------------------------ 工具

    def invoke(self, tool: str, args: Mapping[str, Any]) -> ToolResult:
        """执行一次技能调用。未知工具返回 error，不抛异常。"""
        handler = self._handlers.get(tool)
        if handler is None:
            return ToolResult(
                status="error",
                error_code=ERR_UNKNOWN_TOOL,
                message=f"后端没有实现工具 '{tool}'",
                duration_s=0.0,
            )
        self._call_counts[tool] = self._call_counts.get(tool, 0) + 1
        return handler(args)

    def debug_truth(self) -> Mapping[str, Any]:
        """真值快照——仅供评测与测试。"""
        return self.world.debug_truth()

    # --------------------------------------------------------------- 内部工具

    def _rng(self, tool: str) -> random.Random:
        rng = self._rngs.get(tool)
        if rng is None:
            rng = derive_rng(self.seed, f"duration:{tool}")
            self._rngs[tool] = rng
        return rng

    def _sample_duration(self, tool: str) -> float:
        spec = self.spec.get(tool)
        return spec.duration.sample(self._rng(tool))

    def _next_event(self, tool: str, context: Mapping[str, Any] | None = None):
        index = self._call_counts.get(tool, 1) - 1
        return self.failures.next_event(tool, index, context)

    def _timeout_result(self, tool: str, message: str) -> ToolResult:
        """前置不满足时的统一表现：卡住、超时、状态不变。"""
        spec = self.spec.get(tool)
        self.clock.advance(spec.timeout_s)
        self.metrics.n_timeouts += 1
        self.metrics.n_wasted_calls += 1
        return ToolResult(status="timeout", message=message, duration_s=spec.timeout_s)

    # --------------------------------------------------------------- 技能实现

    def _install(self, args: Mapping[str, Any]) -> ToolResult:
        """一号机器人补装螺丝。"""
        spec = self.spec.get(TOOL_INSTALL)
        box = self.world.box(self.box_id)
        missing = self.target - box.inside
        if self.world.robot("robot1") is not RobotState.OK or box.location is not BoxLocation.STATION:
            return self._timeout_result(
                TOOL_INSTALL, "robot1 未就绪或箱子不在工位，技能卡住"
            )
        event = self._next_event(TOOL_INSTALL, {"missing": missing})
        if event is None or missing <= 0:
            box.inside = self.target
            duration = self._sample_duration(TOOL_INSTALL)
            self.clock.advance(duration)
            return ToolResult(status="done", message="补装完成", duration_s=duration)
        kind = event.kind
        if kind == "STUCK":
            box.inside += int(event.params.get("progress", 0))
            self.world.set_robot("robot1", RobotState.FAULT)
            self.clock.advance(spec.timeout_s)
            self.metrics.n_timeouts += 1
            return ToolResult(status="timeout", message="robot1 卡住", duration_s=spec.timeout_s)
        if kind == "INCOMPLETE":
            box.inside += int(event.params.get("progress", 0))
            duration = self._sample_duration(TOOL_INSTALL)
            self.clock.advance(duration)
            return ToolResult(status="done", message="补装未达目标数", duration_s=duration)
        if kind == "STRAY":
            box.inside = self.target - 1
            box.outside += 1
            duration = self._sample_duration(TOOL_INSTALL)
            self.clock.advance(duration)
            return ToolResult(status="done", message="最后一颗掉在箱外", duration_s=duration)
        # 未知故障类型：按无事件处理。
        box.inside = self.target
        duration = self._sample_duration(TOOL_INSTALL)
        self.clock.advance(duration)
        return ToolResult(status="done", message="补装完成", duration_s=duration)

    def _carry(self, args: Mapping[str, Any]) -> ToolResult:
        """二号机器人把箱子搬到货架。"""
        spec = self.spec.get(TOOL_CARRY)
        box = self.world.box(self.box_id)
        if self.world.robot("robot2") is not RobotState.OK or box.location is not BoxLocation.STATION:
            return self._timeout_result(
                TOOL_CARRY, "robot2 未就绪或箱子不在工位，技能卡住"
            )
        event = self._next_event(TOOL_CARRY)
        if event is None:
            if box.inside != self.target or box.outside > 0:
                self.metrics.unsafe_release = True
            slot = self.world.place_on_shelf(self.box_id)
            if slot is None:
                self.world.set_robot("robot2", RobotState.FAULT)
                self.clock.advance(spec.timeout_s)
                self.metrics.n_timeouts += 1
                return ToolResult(
                    status="timeout", message="货架没有空槽，箱子仍在工位", duration_s=spec.timeout_s
                )
            box.location = BoxLocation.SHELF
            duration = self._sample_duration(TOOL_CARRY)
            self.clock.advance(duration)
            return ToolResult(status="done", message="搬运完成", duration_s=duration)
        kind = event.kind
        if kind == "TIMEOUT":
            self.world.set_robot("robot2", RobotState.FAULT)
            if bool(event.params.get("lost", False)):
                self.world.release_slot(self.box_id)
                box.location = BoxLocation.LOST
            self.clock.advance(spec.timeout_s)
            self.metrics.n_timeouts += 1
            return ToolResult(status="timeout", message="robot2 搬运超时", duration_s=spec.timeout_s)
        if kind == "SILENT_FAIL":
            duration = self._sample_duration(TOOL_CARRY)
            self.clock.advance(duration)
            return ToolResult(status="done", message="搬运完成", duration_s=duration)
        duration = self._sample_duration(TOOL_CARRY)
        self.clock.advance(duration)
        return ToolResult(status="done", message="搬运完成", duration_s=duration)

    def _verify(self, args: Mapping[str, Any]) -> ToolResult:
        """拍照并用验证器检查箱子。"""
        box = self.world.box(self.box_id)
        if box.location is not BoxLocation.STATION:
            self.clock.advance(NOT_AT_STATION_DURATION_S)
            return ToolResult(
                status="error",
                error_code=ERR_BOX_NOT_AT_STATION,
                message="箱子不在工位，无法验证",
                duration_s=NOT_AT_STATION_DURATION_S,
            )
        image = self.images.capture({"inside": box.inside, "outside": box.outside})
        report = self.verifier.verify(image, self._rng(TOOL_VERIFY))
        duration = self.spec.get(TOOL_VERIFY).duration.mean_s
        self.clock.advance(duration)
        return ToolResult(status="ok", message="验证完成", duration_s=duration, data=report.to_dict())

    def _head_image(self, args: Mapping[str, Any]) -> ToolResult:
        """头部相机拍照，只返回图像引用。"""
        box = self.world.box(self.box_id)
        if box.location is not BoxLocation.STATION:
            self.clock.advance(NOT_AT_STATION_DURATION_S)
            return ToolResult(
                status="error",
                error_code=ERR_BOX_NOT_AT_STATION,
                message="箱子不在工位，拍不到箱内",
                duration_s=NOT_AT_STATION_DURATION_S,
            )
        image = self.images.capture({"inside": box.inside, "outside": box.outside})
        duration = self.spec.get(TOOL_HEAD_IMAGE).duration.mean_s
        self.clock.advance(duration)
        return ToolResult(status="ok", message="已拍照", duration_s=duration, data=image.public())

    def _reset(self, args: Mapping[str, Any]) -> ToolResult:
        """机器人复位。永不失败，不改变箱子。"""
        spec = self.spec.get(TOOL_RESET)
        target = str(args.get("robot", ""))
        if target == "all":
            targets = ["robot1", "robot2"]
        else:
            targets = [target]
        duration = self._reset_duration(spec, target, targets)
        for name in targets:
            if self.world.robot(name) is RobotState.OK:
                self.metrics.n_unnecessary_resets += 1
            self.world.set_robot(name, RobotState.OK)
        self.metrics.n_resets += 1
        self.clock.advance(duration)
        return ToolResult(status="ok", message=f"{target} 已复位", duration_s=duration)

    def _reset_duration(self, spec: ToolSpec, target: str, targets: list[str]) -> float:
        durations = spec.reset_durations
        if target in durations:
            return float(durations[target])
        values = [float(durations.get(name, 0.0)) for name in targets]
        return max(values) if values else 0.0

    def _inspector(self, args: Mapping[str, Any]) -> ToolResult:
        """巡检机器人。noop 模式什么都不做；shelf_status 模式返回箱子是否在货架上。"""
        inspector = self.config.inspector
        duration = float(inspector.duration_s)
        self.clock.advance(duration)
        if inspector.mode != "shelf_status":
            return ToolResult(status="done", message="巡检完成（noop）", duration_s=duration)
        on_shelf = any(box.location is BoxLocation.SHELF for box in self.world.boxes.values())
        if inspector.false_negative > 0 and self._rng(TOOL_INSPECTOR).random() < inspector.false_negative:
            on_shelf = False
        return ToolResult(
            status="done",
            message="巡检完成",
            duration_s=duration,
            data={"box_on_shelf": on_shelf},
        )

    def _ask_human(self, args: Mapping[str, Any]) -> ToolResult:
        """向人类求助。"""
        question = str(args.get("question", ""))
        answer = self.human.ask(question, self.world)
        self.clock.advance(answer.duration_s)
        if answer.status == "error":
            return ToolResult(
                status="error",
                error_code=answer.error_code,
                message=answer.message,
                duration_s=answer.duration_s,
            )
        return ToolResult(
            status="ok",
            message=answer.message,
            duration_s=answer.duration_s,
            data={"answer": answer.answer},
        )

    def _finish(self, args: Mapping[str, Any]) -> ToolResult:
        """结束 episode。成功与否由评测按真值判定。"""
        return ToolResult(status="ok", message="已结束", duration_s=0.0)
