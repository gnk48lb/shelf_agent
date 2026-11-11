"""统计工具：比例给 Wilson 95% 置信区间，均值给标准误。"""

from __future__ import annotations

import math
from typing import Sequence

#: 95% 置信区间的 z 值。
Z95 = 1.959963984540054


def wilson_interval(successes: int, total: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score 区间，用于比例的置信区间。

    与「正态近似」相比，在 n 小或比例接近 0/1 时不会越界。
    """
    if total < 0:
        raise ValueError(f"样本量不能为负：{total}")
    if not 0 <= successes <= total:
        raise ValueError(f"成功数 {successes} 必须落在 [0, {total}] 内")
    if total == 0:
        return (0.0, 0.0)
    n = float(total)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def mean_and_stderr(values: Sequence[float]) -> tuple[float, float]:
    """返回 (均值, 标准误)。空序列时返回 (0.0, 0.0)。"""
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    mean = sum(values) / n
    if n == 1:
        return (mean, 0.0)
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return (mean, math.sqrt(var / n))


def summarize_rate(successes: int, total: int, z: float = Z95) -> dict[str, float]:
    """把比例与其置信区间打包成字典，便于写进报告。"""
    lo, hi = wilson_interval(successes, total, z)
    return {
        "rate": (successes / total) if total else 0.0,
        "ci_low": lo,
        "ci_high": hi,
        "n": float(total),
        "count": float(successes),
    }
