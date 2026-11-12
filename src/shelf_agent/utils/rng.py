"""由 seed 派生的独立随机流。

每个技能、每个验证器各用一条流，保证「换策略不会改变某技能第 n 次调用的
故障命运」。派生方式为 ``sha256(f"{seed}:{stream}")``，与 ``random.seed`` 的
内部实现无关，因此跨 Python 版本稳定。

**流命名规则**（全部是 ``derive_rng(seed, 流名)`` 的流名）：

======================== ================================================
``failure:<skill>``      故障注入流，每个技能一条（``env/failures.py``）
``duration:<tool>``      技能耗时抖动流（``env/sim_backend.py``）
``verifier:noise``       验证器噪声流（``perception/verifier.py``）
``inspector:noise``      巡检机器人误报流（``env/sim_backend.py``）
======================== ================================================

**纯度要求**：一条流上的抽取次数必须是固定的，不能「按分支决定要不要抽」——
否则同一条流的后续位置会依赖上下文，「第 n 次调用的命运」就不再与上下文无关
（见 ``tests/test_random_stream_purity.py``）。
"""

from __future__ import annotations

import hashlib
import random

#: 故障注入流的流名前缀。
STREAM_FAILURE = "failure:"
#: 耗时抖动流的流名前缀。
STREAM_DURATION = "duration:"
#: 验证器噪声流的流名。
STREAM_VERIFIER_NOISE = "verifier:noise"
#: 巡检误报流的流名。
STREAM_INSPECTOR_NOISE = "inspector:noise"


def derive_digest(seed: int, stream: str) -> str:
    """返回 ``sha256(f"{seed}:{stream}")`` 的十六进制摘要。"""
    return hashlib.sha256(f"{seed}:{stream}".encode("utf-8")).hexdigest()


def derive_seed(seed: int, stream: str) -> int:
    """把摘要折叠成一个 64 位整数种子。"""
    return int(derive_digest(seed, stream)[:16], 16)


def derive_rng(seed: int, stream: str) -> random.Random:
    """派生一条独立的随机流。同一 (seed, stream) 永远得到同一条流。"""
    return random.Random(derive_seed(seed, stream))


def derive_rngs(seed: int, streams: list[str]) -> dict[str, random.Random]:
    """一次派生多条流（键为流名）。"""
    return {name: derive_rng(seed, name) for name in streams}
