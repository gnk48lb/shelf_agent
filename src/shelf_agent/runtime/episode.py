"""按配置装配一个 episode 的全部部件。

这是唯一一处「把配置变成可运行的世界」的地方：世界、后端、验证器、人类、
故障模型与执行器都在这里组装，测试、评测与将来的 RL 环境共用同一套装配逻辑。

放在 ``runtime``（L3）而不是 ``eval``（L5），是为了让 ``runtime/tool_env.py``
的 gym 风格环境也能用它——分层只允许向下依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..config import AppConfig, build_config, load_config
from ..contracts.spec import ToolSpecSet, build_spec_set
from ..contracts.types import ToolCall, ToolResult
from ..env.failures import FailureModel, RandomFailureModel
from ..env.human import ScriptedHuman
from ..env.sim_backend import SimBackend
from ..env.world import World, make_world
from ..perception.completion import Report
from ..perception.imagelib import ImageLibrary, SyntheticImageLibrary
from ..perception.verifier import (
    NoisyVerifier,
    OracleVerifier,
    ScriptedVerifier,
    Verifier,
    load_confusion_table,
)
from ..utils.clock import VirtualClock
from .budget import BudgetState
from .executor import ToolExecutor
from .trajectory import Trajectory

#: 默认的箱子与槽位清单（real_screw：1 箱 1 槽）。
DEFAULT_BOX_IDS = ("box_1",)
DEFAULT_SLOT_IDS = ("slot_1",)
DEFAULT_ROBOTS = ("robot1", "robot2")


@dataclass
class Episode:
    """一个装配好的 episode。"""

    config: AppConfig
    spec: ToolSpecSet
    world: World
    backend: SimBackend
    executor: ToolExecutor
    trajectory: Trajectory
    budget: BudgetState
    clock: VirtualClock

    @property
    def box_id(self) -> str:
        return self.backend.box_id

    def truth(self) -> Mapping[str, Any]:
        """真值快照（评测与测试专用）。"""
        return self.backend.debug_truth()

    def run(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用。"""
        return self.executor.execute(call)


def make_verifier(
    config: AppConfig,
    kind: str | None = None,
    reports: Sequence[Mapping[str, Any]] | None = None,
) -> Verifier:
    """按配置构造验证器。"""
    chosen = kind or config.verifier.kind
    if chosen == "oracle":
        return OracleVerifier()
    if chosen == "noisy":
        table = None
        table_path = config.verifier.confusion_table_path
        if table_path:
            table = load_confusion_table(Path(table_path))
        return NoisyVerifier(
            target=config.target_screws,
            p_inside_correct=config.verifier.p_inside_correct,
            boundary_confusion=config.verifier.boundary_confusion,
            p_unclear=config.verifier.p_unclear,
            p_outside_miss=config.verifier.p_outside_miss,
            confusion_table=table,
        )
    if chosen == "scripted":
        raw = list(reports) if reports is not None else list(config.verifier.scripted_reports)
        return ScriptedVerifier([Report.from_dict(item) for item in raw])
    raise ValueError(f"未知的验证器类型 '{chosen}'")


def make_failures(
    config: AppConfig,
    seed: int,
    script: Mapping[str, Sequence[Any]] | None = None,
    rate: float | None = None,
) -> FailureModel:
    """构造故障模型：给了脚本就用脚本，否则用按 rate 的随机注入。"""
    if script is not None:
        return FailureModel.scripted(script)
    effective_rate = config.failures.rate if rate is None else float(rate)
    return RandomFailureModel(
        seed=seed,
        rate=effective_rate,
        skills=config.failures.skills,
        p_lost=config.failures.p_lost,
    )


def make_images(save_images: bool = False, out_dir: str | Path = "out/images") -> ImageLibrary:
    """构造图像库。"""
    return SyntheticImageLibrary(save_images=save_images, out_dir=out_dir)


def make_episode(
    config: AppConfig,
    seed: int = 0,
    failure_script: Mapping[str, Sequence[Any]] | None = None,
    failure_rate: float | None = None,
    verifier_kind: str | None = None,
    verifier_reports: Sequence[Mapping[str, Any]] | None = None,
    save_images: bool = False,
    image_dir: str | Path = "out/images",
    box_ids: Sequence[str] = DEFAULT_BOX_IDS,
    slot_ids: Sequence[str] = DEFAULT_SLOT_IDS,
    robots: Sequence[str] = DEFAULT_ROBOTS,
) -> Episode:
    """装配一个 episode。"""
    spec = build_spec_set(config)
    world = make_world(list(box_ids), list(slot_ids), list(robots))
    clock = VirtualClock()
    human = ScriptedHuman(latency_s=config.human.latency_s, max_asks=config.human.max_asks)
    failures = make_failures(config, seed, script=failure_script, rate=failure_rate)
    images = make_images(save_images=save_images, out_dir=image_dir)
    verifier = make_verifier(config, kind=verifier_kind, reports=verifier_reports)
    backend = SimBackend(
        config=config,
        spec=spec,
        world=world,
        failures=failures,
        images=images,
        verifier=verifier,
        human=human,
        clock=clock,
        seed=seed,
        box_id=box_ids[0],
    )
    budget = BudgetState.from_config(config.budget)
    trajectory = Trajectory()
    executor = ToolExecutor(
        backend=backend, spec=spec, budget=budget, trajectory=trajectory, clock=clock
    )
    return Episode(
        config=config,
        spec=spec,
        world=world,
        backend=backend,
        executor=executor,
        trajectory=trajectory,
        budget=budget,
        clock=clock,
    )


def load_episode_config(path: str | Path, overrides: Mapping[str, Any] | None = None) -> AppConfig:
    """读配置并应用顶层覆盖。"""
    config = load_config(path)
    if overrides:
        config = build_config({**config.raw, **overrides}, source_path=config.source_path)
    return config
