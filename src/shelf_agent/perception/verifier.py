"""验证器：把一张图变成 ``{inside, outside, clear}`` 报告。

三种实现：

* :class:`OracleVerifier` —— 直接读真值，完全准确（**仅仿真用**）。
* :class:`NoisyVerifier` —— 模拟真 VLM 验证器的噪声通道。
* :class:`ScriptedVerifier` —— 按脚本逐次返回报告（测试与演示用）。

留好 :class:`Verifier` 协议以便将来接 VLM 验证器；本期不实现。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .completion import Report
from .imagelib import ImageRef


class Verifier(Protocol):
    """验证器接口。"""

    def verify(self, image_ref: ImageRef, rng: random.Random) -> Report:
        """看图给出报告。``rng`` 是验证器自己的随机流。"""


class OracleVerifier:
    """直接读真值的验证器——完全准确，但**只存在于仿真里**。

    真机上没有这个实现；它用于区分「agent 决策错了」与「感知错了」。
    """

    def verify(self, image_ref: ImageRef, rng: random.Random) -> Report:
        meta = image_ref.meta
        return Report(
            inside=int(meta.get("inside", 0)),
            outside=int(meta.get("outside", 0)),
            clear=True,
        )


class NoisyVerifier:
    """带噪声的验证器。

    规则（§3.4）：

    1. 以 ``p_inside_correct`` 的概率报出真值，否则报 ``真值 ± 1``（等概率，下限 0）；
    2. 真值落在 ``N ± 1`` 时，再以 ``boundary_confusion`` 的概率覆盖成 ``N``
       ——这就是「错误放行」的来源；
    3. 真值 ``outside > 0`` 时，以 ``p_outside_miss`` 的概率漏报成 0；
    4. 以 ``p_unclear`` 的概率给出 ``clear=False``。
    """

    def __init__(
        self,
        target: int,
        p_inside_correct: float = 0.95,
        boundary_confusion: float = 0.10,
        p_unclear: float = 0.03,
        p_outside_miss: float = 0.05,
        confusion_table: Mapping[int, Mapping[int, float]] | None = None,
    ) -> None:
        self.target = int(target)
        self.p_inside_correct = p_inside_correct
        self.boundary_confusion = boundary_confusion
        self.p_unclear = p_unclear
        self.p_outside_miss = p_outside_miss
        self.confusion_table = confusion_table

    @classmethod
    def from_confusion_table(
        cls,
        table: Mapping[int, Mapping[int, float]] | str | Path,
        target: int,
        **kwargs: Any,
    ) -> "NoisyVerifier":
        """用经验混淆表构造。

        表的格式是 ``{真值: {预测值: 概率}}``；``table`` 可以是映射，也可以是
        JSON 文件路径。表里没有真值这一行时，退回参数化噪声模型。
        """
        if isinstance(table, (str, Path)):
            loaded = load_confusion_table(table)
        else:
            loaded = {int(k): {int(pk): float(pv) for pk, pv in v.items()} for k, v in table.items()}
        return cls(target=target, confusion_table=loaded, **kwargs)

    def _predict_inside(self, true_inside: int, rng: random.Random) -> int:
        inside: int | None = None
        if self.confusion_table:
            row = self.confusion_table.get(true_inside)
            if row:
                inside = _sample_from_distribution(row, rng)
        if inside is None:
            if rng.random() < self.p_inside_correct:
                inside = true_inside
            else:
                delta = 1 if rng.random() < 0.5 else -1
                inside = max(0, true_inside + delta)
        # 边界混淆：真值是 N±1 时有一定概率被「纠正」成 N（错误放行）。
        if abs(true_inside - self.target) == 1 and rng.random() < self.boundary_confusion:
            inside = self.target
        return inside

    def _predict_outside(self, true_outside: int, rng: random.Random) -> int:
        if true_outside <= 0:
            return 0
        if rng.random() < self.p_outside_miss:
            return 0
        return true_outside

    def verify(self, image_ref: ImageRef, rng: random.Random) -> Report:
        true_inside = int(image_ref.meta.get("inside", 0))
        true_outside = int(image_ref.meta.get("outside", 0))
        return Report(
            inside=self._predict_inside(true_inside, rng),
            outside=self._predict_outside(true_outside, rng),
            clear=rng.random() >= self.p_unclear,
        )


def _sample_from_distribution(dist: Mapping[int, float], rng: random.Random) -> int:
    """按概率分布抽一个键。"""
    items = sorted(dist.items())
    total = sum(p for _, p in items)
    if total <= 0:
        return items[0][0]
    threshold = rng.random() * total
    acc = 0.0
    for key, prob in items:
        acc += prob
        if threshold < acc:
            return key
    return items[-1][0]


def load_confusion_table(path: str | Path) -> dict[int, dict[int, float]]:
    """读取 ``{true_inside: {pred_inside: prob}}`` 形式的经验混淆表。"""
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"混淆表顶层必须是对象，实际是 {type(raw).__name__}：{p}")
    table: dict[int, dict[int, float]] = {}
    for true_key, row in raw.items():
        if not isinstance(row, dict):
            raise ValueError(f"混淆表 '{true_key}' 行必须是对象：{p}")
        table[int(true_key)] = {int(k): float(v) for k, v in row.items()}
    return table


class ScriptedVerifier:
    """按脚本逐次返回报告。

    脚本耗尽后重复最后一条报告（保证演示与测试不会因为多调一次 verify 而崩）。
    """

    def __init__(self, reports: Sequence[Report | Mapping[str, Any]]) -> None:
        parsed: list[Report] = []
        for item in reports:
            parsed.append(item if isinstance(item, Report) else Report.from_dict(item))
        if not parsed:
            raise ValueError("ScriptedVerifier 至少要有一条报告")
        self._reports = parsed
        self._index = 0

    @property
    def calls(self) -> int:
        """已被消费的报告条数。"""
        return self._index

    def verify(self, image_ref: ImageRef, rng: random.Random) -> Report:
        if self._index < len(self._reports):
            report = self._reports[self._index]
            self._index += 1
        else:
            report = self._reports[-1]
        return report
