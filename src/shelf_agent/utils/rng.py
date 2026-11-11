"""由 seed 派生的独立随机流。

每个技能、每个验证器各用一条流，保证「换策略不会改变某技能第 n 次调用的
故障命运」。派生方式为 ``sha256(f"{seed}:{stream}")``，与 ``random.seed`` 的
内部实现无关，因此跨 Python 版本稳定。
"""

from __future__ import annotations

import hashlib
import random

#: 常用的流名字符串：各技能一条、验证器一条、人类一条等。
STREAM_INSTALL = "skill.robot1_install_screws"
STREAM_CARRY = "skill.robot2_carry_box"
STREAM_VERIFIER = "verifier.noisy"
STREAM_HUMAN = "human.scripted"
STREAM_FUZZ = "fuzz.random"


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
