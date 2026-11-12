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
from ..utils.rng import (
    STREAM_DURATION,
    STREAM_INSPECTOR_NOISE,
    STREAM_VERIFIER_NOISE,
    derive_rng,
)
from .failures import FailureModel
from .human import ScriptedHuman
from .world import BoxState, World

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

#: 工具返回的固定文案（§A1）。
#:
#: 物理技能的 message **只能取决于 status**——文案里不得出现任何隐藏状态
#: （装没装满、散没散落、箱子在哪）。否则 agent 能从 message 里读出故障，
#: 「必须验证才能发现隐藏故障」这个实验前提就没了。
MSG_DONE = "done"
MSG_TIMEOUT = "timeout"
MSG_OK = "ok"

#: 允许出现在 message 里的例外：相机直接观察到的事实（A5 的观测通道）。
MSG_BOX_NOT_AT_STATION = "箱子不在工位，无法验证"

#: 全部允许的文案（白名单；测试与文档共用这一份）。
ALLOWED_MESSAGES = frozenset({MSG_DONE, MSG_TIMEOUT, MSG_OK, MSG_BOX_NOT_AT_STATION})


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
        self._last_event: dict[str, Any] | None = None
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
        self._last_event = None
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
        """真值快照——仅供评测与测试。

        ``last_event`` 是本次调用注入的事件或前置违规原因，**只存在于真值通道**，
        永远不进 ``ToolResult``（§A2）。
        """
        snapshot = dict(self.world.debug_truth())
        snapshot["last_event"] = self._last_event
        return snapshot

    def _note_event(self, event: Any) -> None:
        """记下本次注入的故障事件（真值通道）。"""
        self._last_event = {
            "type": "injected",
            "kind": event.kind,
            "params": dict(event.params),
        }

    def _note_precondition(self, reason: str) -> None:
        """记下本次前置违规的原因（真值通道）。"""
        self._last_event = {"type": "precondition", "reason": reason}

    def _precondition_reason(self, robot: str, box: BoxState) -> str | None:
        """前置是否满足；不满足时给出可读的原因（仅真值通道使用）。"""
        if self.world.robot(robot) is not RobotState.OK:
            return f"{robot}=FAULT"
        if box.location is not BoxLocation.STATION:
            return f"box_location={box.location.value}"
        return None

    # --------------------------------------------------------------- 内部工具

    def _rng(self, name: str) -> random.Random:
        """按流名取随机流（流命名规则见 ``utils/rng.py``）。"""
        rng = self._rngs.get(name)
        if rng is None:
            rng = derive_rng(self.seed, name)
            self._rngs[name] = rng
        return rng

    def _duration_rng(self, tool: str) -> random.Random:
        """耗时抖动流：``duration:<tool>``。"""
        return self._rng(f"{STREAM_DURATION}{tool}")

    def _sample_duration(self, tool: str) -> float:
        spec = self.spec.get(tool)
        return spec.duration.sample(self._duration_rng(tool))

    def _next_event(self, tool: str, context: Mapping[str, Any] | None = None):
        index = self._call_counts.get(tool, 1) - 1
        return self.failures.next_event(tool, index, context)

    def _box_for(self, args: Mapping[str, Any]) -> BoxState:
        """按调用参数取箱子（多箱配置用 ``box_id``；单箱配置忽略）。"""
        box_id = str(args.get("box_id") or self.box_id)
        return self.world.box(box_id)

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
        box = self._box_for(args)
        missing = self.target - box.inside
        reason = self._precondition_reason("robot1", box)
        if reason is not None:
            self._note_precondition(reason)
            return self._timeout_result(TOOL_INSTALL, MSG_TIMEOUT)
        event = self._next_event(TOOL_INSTALL, {"missing": missing})
        if event is None or missing <= 0:
            box.inside = self.target
            duration = self._sample_duration(TOOL_INSTALL)
            self.clock.advance(duration)
            return ToolResult(status="done", message=MSG_DONE, duration_s=duration)
        self._note_event(event)
        kind = event.kind
        if kind == "STUCK":
            box.inside += int(event.params.get("progress", 0))
            self.world.set_robot("robot1", RobotState.FAULT)
            self.clock.advance(spec.timeout_s)
            self.metrics.n_timeouts += 1
            return ToolResult(status="timeout", message=MSG_TIMEOUT, duration_s=spec.timeout_s)
        if kind == "INCOMPLETE":
            box.inside += int(event.params.get("progress", 0))
            duration = self._sample_duration(TOOL_INSTALL)
            self.clock.advance(duration)
            return ToolResult(status="done", message=MSG_DONE, duration_s=duration)
        if kind == "STRAY":
            box.inside = self.target - 1
            box.outside += 1
            duration = self._sample_duration(TOOL_INSTALL)
            self.clock.advance(duration)
            return ToolResult(status="done", message=MSG_DONE, duration_s=duration)
        # 未知故障类型：按无事件处理。
        box.inside = self.target
        duration = self._sample_duration(TOOL_INSTALL)
        self.clock.advance(duration)
        return ToolResult(status="done", message=MSG_DONE, duration_s=duration)

    def _carry(self, args: Mapping[str, Any]) -> ToolResult:
        """二号机器人把箱子搬到货架。"""
        spec = self.spec.get(TOOL_CARRY)
        box = self._box_for(args)
        reason = self._precondition_reason("robot2", box)
        if reason is not None:
            self._note_precondition(reason)
            return self._timeout_result(TOOL_CARRY, MSG_TIMEOUT)
        event = self._next_event(TOOL_CARRY)
        if event is None or event.kind not in ("TIMEOUT", "SILENT_FAIL"):
            # 无事件（或未知故障类型）：正常搬运。
            if box.inside != self.target or box.outside > 0:
                self.metrics.unsafe_release = True
            slot = self.world.place_on_shelf(box.box_id, args.get("slot_id"))
            if slot is None:
                self.world.set_robot("robot2", RobotState.FAULT)
                self._note_precondition("no_free_slot")
                self.clock.advance(spec.timeout_s)
                self.metrics.n_timeouts += 1
                return ToolResult(status="timeout", message=MSG_TIMEOUT, duration_s=spec.timeout_s)
            box.location = BoxLocation.SHELF
            duration = self._sample_duration(TOOL_CARRY)
            self.clock.advance(duration)
            return ToolResult(status="done", message=MSG_DONE, duration_s=duration)
        self._note_event(event)
        kind = event.kind
        if kind == "TIMEOUT":
            self.world.set_robot("robot2", RobotState.FAULT)
            if bool(event.params.get("lost", False)):
                self.world.release_slot(box.box_id)
                box.location = BoxLocation.LOST
            self.clock.advance(spec.timeout_s)
            self.metrics.n_timeouts += 1
            return ToolResult(status="timeout", message=MSG_TIMEOUT, duration_s=spec.timeout_s)
        # SILENT_FAIL：返回 done，但箱子仍在工位。
        duration = self._sample_duration(TOOL_CARRY)
        self.clock.advance(duration)
        return ToolResult(status="done", message=MSG_DONE, duration_s=duration)

    def _verify(self, args: Mapping[str, Any]) -> ToolResult:
        """拍照并用验证器检查箱子。"""
        box = self._box_for(args)
        if box.location is not BoxLocation.STATION:
            self.clock.advance(NOT_AT_STATION_DURATION_S)
            return ToolResult(
                status="error",
                error_code=ERR_BOX_NOT_AT_STATION,
                message=MSG_BOX_NOT_AT_STATION,
                duration_s=NOT_AT_STATION_DURATION_S,
            )
        image = self.images.capture({"inside": box.inside, "outside": box.outside})
        report = self.verifier.verify(image, self._rng(STREAM_VERIFIER_NOISE))
        duration = self.spec.get(TOOL_VERIFY).duration.mean_s
        self.clock.advance(duration)
        return ToolResult(status="ok", message=MSG_OK, duration_s=duration, data=report.to_dict())

    def _head_image(self, args: Mapping[str, Any]) -> ToolResult:
        """头部相机拍照，只返回图像引用。"""
        box = self._box_for(args)
        if box.location is not BoxLocation.STATION:
            self.clock.advance(NOT_AT_STATION_DURATION_S)
            return ToolResult(
                status="error",
                error_code=ERR_BOX_NOT_AT_STATION,
                message=MSG_BOX_NOT_AT_STATION,
                duration_s=NOT_AT_STATION_DURATION_S,
            )
        image = self.images.capture({"inside": box.inside, "outside": box.outside})
        duration = self.spec.get(TOOL_HEAD_IMAGE).duration.mean_s
        self.clock.advance(duration)
        return ToolResult(status="ok", message=MSG_OK, duration_s=duration, data=image.public())

    def _reset(self, args: Mapping[str, Any]) -> ToolResult:
        """机器人复位。永不失败，不改变箱子。"""
        spec = self.spec.get(TOOL_RESET)
        target = str(args.get("robot", ""))
        if target == "all":
            targets = ["robot1", "robot2"]
        else:
            targets = [target]
        duration = self._reset_duration(spec, target, targets)
        # 「不必要」按**调用**计：只有所有目标机器人此前都是 OK 才算一次（§B3）。
        all_ok = all(self.world.robot(name) is RobotState.OK for name in targets)
        for name in targets:
            self.world.set_robot(name, RobotState.OK)
        if all_ok:
            self.metrics.n_unnecessary_resets += 1
        self.metrics.n_resets += 1
        self.clock.advance(duration)
        return ToolResult(status="ok", message=MSG_OK, duration_s=duration)

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
            return ToolResult(status="done", message=MSG_DONE, duration_s=duration)
        on_shelf = any(box.location is BoxLocation.SHELF for box in self.world.boxes.values())
        if inspector.false_negative > 0 and self._rng(STREAM_INSPECTOR_NOISE).random() < inspector.false_negative:
            on_shelf = False
        return ToolResult(
            status="done",
            message=MSG_DONE,
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
        return ToolResult(status="ok", message=MSG_OK, duration_s=0.0)
