"""故障注入模型。

两种模式：

* :class:`RandomFailureModel` —— 按 ``rate`` 与权重随机注入（sweep 用）。
* :class:`ScriptedFailureModel` —— 按脚本逐次注入（测试与演示用）。

每个技能一条独立随机流，保证「换策略不会改变某技能第 n 次调用的故障命运」。
"""

from __future__ import annotations

import abc
import random
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..config import FailureSkillConfig
from ..utils.rng import STREAM_FAILURE, derive_rng

#: 各类故障的默认参数。
DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "STUCK": {"progress": 0},
    "INCOMPLETE": {"progress": None},  # None 表示取 missing - 1
    "STRAY": {},
    "TIMEOUT": {"lost": False},
    "SILENT_FAIL": {},
}


@dataclass(frozen=True)
class FailureEvent:
    """一次故障事件。``params`` 是该故障类型的参数。"""

    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)


class FailureModel(abc.ABC):
    """故障模型接口。"""

    @abc.abstractmethod
    def next_event(
        self,
        skill: str,
        call_index: int,
        context: Mapping[str, Any] | None = None,
    ) -> FailureEvent | None:
        """返回本次调用要注入的故障；``None`` 表示没有事件。

        ``context`` 随技能不同：install 会带上 ``missing``（还差几颗）。
        """

    @classmethod
    def scripted(
        cls,
        script: Mapping[str, Sequence[Any]],
        fallback: "FailureModel | None" = None,
    ) -> "ScriptedFailureModel":
        """构造脚本化模型。

        ``script`` 的值是按调用顺序消费的事件列表：``None`` 表示无事件，
        字符串是默认参数的简写，``(kind, params)`` 是显式参数。
        """
        return ScriptedFailureModel(script, fallback=fallback)


def _choose_kind(u: float, weights: Mapping[str, float]) -> str:
    """按权重抽一个故障类型。``u`` 是预先抽好的均匀随机数（消耗固定）。"""
    items = [(kind, float(w)) for kind, w in sorted(weights.items()) if w > 0]
    if not items:
        return ""
    total = sum(w for _, w in items)
    threshold = u * total
    acc = 0.0
    for kind, weight in items:
        acc += weight
        if threshold < acc:
            return kind
    return items[-1][0]


def _progress_from_unit(u: float, missing: int) -> int:
    """由均匀随机数推出进度，落在 ``[0, missing-1]``。"""
    if missing <= 1:
        return 0
    return min(missing - 1, int(u * missing))


class RandomFailureModel(FailureModel):
    """按概率随机注入故障。

    每个技能一条独立随机流（``failure:<skill>``），**每次调用无条件抽固定
    3 个**均匀随机数（``u_event`` / ``u_kind`` / ``u_param``），分支只影响
    怎么用这 3 个数，不影响抽几个——否则同一条流的后续位置会依赖上下文，
    「第 n 次调用的故障命运」就不再与上下文无关（§B1）。
    """

    #: 每次 ``next_event`` 无条件抽取的均匀随机数个数。
    DRAWS_PER_EVENT = 3

    def __init__(
        self,
        seed: int,
        rate: float = 0.0,
        skills: Mapping[str, FailureSkillConfig] | None = None,
        p_lost: float = 0.0,
    ) -> None:
        self.seed = seed
        self.rate = float(rate)
        self.skills = dict(skills or {})
        self.p_lost = float(p_lost)
        self._rngs: dict[str, random.Random] = {}

    def _rng(self, skill: str) -> random.Random:
        rng = self._rngs.get(skill)
        if rng is None:
            rng = derive_rng(self.seed, f"{STREAM_FAILURE}{skill}")
            self._rngs[skill] = rng
        return rng

    def next_event(
        self,
        skill: str,
        call_index: int,
        context: Mapping[str, Any] | None = None,
    ) -> FailureEvent | None:
        rng = self._rng(skill)
        # 无条件抽固定个数：即使这次不触发、即使技能没配置，也照抽。
        u_event = rng.random()
        u_kind = rng.random()
        u_param = rng.random()
        cfg = self.skills.get(skill)
        rate = cfg.effective_rate(self.rate) if cfg else self.rate
        if u_event >= rate:
            return None
        kind = _choose_kind(u_kind, cfg.weights if cfg else {})
        if not kind:
            return None
        params: dict[str, Any] = {}
        if kind == "TIMEOUT":
            params["lost"] = u_param < self.p_lost
        elif kind in ("STUCK", "INCOMPLETE"):
            missing = int((context or {}).get("missing", 1))
            params["progress"] = _progress_from_unit(u_param, missing)
        return FailureEvent(kind=kind, params=params)


def parse_event(spec: Any, context: Mapping[str, Any] | None = None) -> FailureEvent | None:
    """把脚本里的一条事件规格解析成 :class:`FailureEvent`。

    * ``None`` → 无事件；
    * ``"STUCK"`` → 该类型的默认参数；
    * ``("INCOMPLETE", {"progress": 2})`` → 显式参数。
    """
    if spec is None:
        return None
    if isinstance(spec, FailureEvent):
        return spec
    if isinstance(spec, str):
        kind, params = spec, {}
    elif isinstance(spec, Mapping):
        kind = spec.get("kind")
        if not isinstance(kind, str):
            raise ValueError(f"故障事件字典必须含字符串 'kind'，实际是 {spec!r}")
        params = dict(spec.get("params") or {})
    elif isinstance(spec, (tuple, list)) and len(spec) == 2:
        kind, raw = spec
        if not isinstance(kind, str):
            raise ValueError(f"故障事件元组的第一个元素必须是字符串，实际是 {kind!r}")
        if not isinstance(raw, Mapping):
            raise ValueError(f"故障事件元组的第二个元素必须是映射，实际是 {type(raw).__name__}")
        params = dict(raw)
    else:
        raise ValueError(f"无法解析的故障事件规格：{spec!r}")
    defaults = dict(DEFAULT_PARAMS.get(kind, {}))
    merged = {**defaults, **params}
    if merged.get("progress", 0) is None:
        missing = int((context or {}).get("missing", 1))
        merged["progress"] = max(0, missing - 1)
    return FailureEvent(kind=kind, params=merged)


class ScriptedFailureModel(FailureModel):
    """脚本化故障注入：按调用顺序消费事件，耗尽后恢复正常。

    没有配置脚本的技能走 ``fallback``（若提供），否则不注入任何事件。
    """

    def __init__(
        self,
        script: Mapping[str, Sequence[Any]],
        fallback: FailureModel | None = None,
    ) -> None:
        self._script = {skill: list(items) for skill, items in script.items()}
        self._index = {skill: 0 for skill in self._script}
        self.fallback = fallback

    def next_event(
        self,
        skill: str,
        call_index: int,
        context: Mapping[str, Any] | None = None,
    ) -> FailureEvent | None:
        events = self._script.get(skill)
        if events is None:
            if self.fallback is not None:
                return self.fallback.next_event(skill, call_index, context)
            return None
        index = self._index.get(skill, 0)
        if index >= len(events):
            if self.fallback is not None:
                return self.fallback.next_event(skill, call_index, context)
            return None
        self._index[skill] = index + 1
        return parse_event(events[index], context)

    def consumed(self, skill: str) -> int:
        """某技能已消费的事件条数。"""
        return self._index.get(skill, 0)
