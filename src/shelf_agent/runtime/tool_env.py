"""gym 风格的环境（为将来的 RL 预留）。

接口刻意做得很薄::

    env = ToolEnv(config)
    obs, info = env.reset(seed=0)
    obs, reward, terminated, truncated, info = env.step(ToolCall("verify_box"))

观察里**只有工具结果**（与规则策略、LLM 看到的东西完全一致）；``reward`` 才用
真值算——它是训练信号，不是观察。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..config import AppConfig
from ..contracts.types import ToolCall
from ..perception.completion import truth_success
from .episode import DEFAULT_BOX_IDS, DEFAULT_ROBOTS, DEFAULT_SLOT_IDS, Episode, make_episode

#: 每步的时间代价（让「尽快完成任务」比「拖着」更有利）。
STEP_TIME_COST = 0.01

#: 达成目标时的奖励。
GOAL_REWARD = 1.0


class ToolEnv:
    """工具调用环境。"""

    def __init__(
        self,
        config: AppConfig,
        failure_rate: float | None = None,
        verifier_kind: str | None = "oracle",
        box_ids: Sequence[str] = DEFAULT_BOX_IDS,
        slot_ids: Sequence[str] = DEFAULT_SLOT_IDS,
        robots: Sequence[str] = DEFAULT_ROBOTS,
    ) -> None:
        self.config = config
        self.failure_rate = failure_rate
        self.verifier_kind = verifier_kind
        self.box_ids = tuple(box_ids)
        self.slot_ids = tuple(slot_ids)
        self.robots = tuple(robots)
        self.episode: Episode | None = None
        self._steps = 0

    # ------------------------------------------------------------------ gym

    @property
    def max_steps(self) -> int:
        """最大步数（与预算口径一致）。"""
        return self.config.budget.soft_limit + self.config.budget.hard_extra

    def reset(self, seed: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """重置环境。``seed`` 决定故障注入的随机流。"""
        self.episode = make_episode(
            self.config,
            seed=int(seed or 0),
            failure_rate=self.failure_rate,
            verifier_kind=self.verifier_kind,
            box_ids=self.box_ids,
            slot_ids=self.slot_ids,
            robots=self.robots,
        )
        self._steps = 0
        obs: dict[str, Any] = {
            "t_s": 0.0,
            "steps": 0,
            "message": "工位就绪：先装螺丝，再验证，最后搬到货架",
        }
        return obs, {"seed": int(seed or 0)}

    def step(
        self,
        call: ToolCall,
    ) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        """执行一次工具调用。"""
        if self.episode is None:
            raise RuntimeError("必须先调用 reset(seed)")
        episode = self.episode
        result = episode.executor.execute(call)
        self._steps += 1
        obs: dict[str, Any] = {
            "t_s": round(episode.clock.time_s, 6),
            "steps": self._steps,
            "status": result.status,
            "error_code": result.error_code,
            "message": result.message,
            "duration_s": result.duration_s,
            "data": dict(result.data),
        }
        reward = self._reward()
        terminated = call.name == "finish" or episode.budget.aborted
        truncated = (not terminated) and self._steps >= self.max_steps
        info: dict[str, Any] = {
            "tool_counts": episode.trajectory.tool_counts(),
            "n_invalid_calls": episode.budget.invalid_calls,
        }
        return obs, reward, terminated, truncated, info

    # ------------------------------------------------------------------ 内部

    def _reward(self) -> float:
        """奖励：真值成功给 1，每步扣一点时间代价。"""
        if self.episode is None:  # pragma: no cover - 上面的 reset 已保证
            return 0.0
        box = self.episode.world.box(self.episode.box_id)
        success = truth_success(
            box.location.value, box.inside, box.outside, self.config.target_screws
        )
        return (GOAL_REWARD if success else 0.0) - STEP_TIME_COST

    @property
    def truth(self) -> Mapping[str, Any]:
        """真值快照（评测与调试用）。"""
        if self.episode is None:
            raise RuntimeError("必须先调用 reset(seed)")
        return self.episode.truth()
