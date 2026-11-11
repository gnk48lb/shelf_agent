"""utils 层单测：随机流派生、虚拟时钟、JSONL 往返、Wilson 区间。"""

from __future__ import annotations

import math

import pytest

from shelf_agent.utils.clock import VirtualClock
from shelf_agent.utils.jsonl import append_jsonl, dumps, iter_jsonl, read_jsonl, write_jsonl
from shelf_agent.utils.rng import derive_digest, derive_rng, derive_seed
from shelf_agent.utils.stats import mean_and_stderr, wilson_interval

# 固定的 sha256 期望值（防止派生方式被悄悄改掉）。
EXPECTED_42_INSTALL = "024fcef34e7f833fe1b46fa542a8f3c3e48b28bc6805691f0bbd17e958add842"
EXPECTED_42_CARRY = "5b831281610b386dca710621ce7d5a989222fbb0083634829e2299d4a70b2e30"
EXPECTED_0_SEED = "d91bf65986142d5408c942eefc2bb0e4cb849268a609653357b1941dcd593376"


def test_sha256_derivation_is_stable() -> None:
    assert derive_digest(42, "install") == EXPECTED_42_INSTALL
    assert derive_digest(42, "carry") == EXPECTED_42_CARRY
    assert derive_digest(0, "seed") == EXPECTED_0_SEED


def test_derive_seed_folds_digest_to_int() -> None:
    assert derive_seed(42, "install") == int(EXPECTED_42_INSTALL[:16], 16)
    assert derive_seed(42, "install") == derive_seed(42, "install")


def test_derived_streams_are_independent() -> None:
    """同 seed 不同流必须给出不同随机序列。"""
    a = [derive_rng(7, "skill.install").random() for _ in range(1)]
    b = [derive_rng(7, "skill.carry").random() for _ in range(1)]
    assert a != b
    # 换种子也应改变序列
    assert derive_rng(7, "skill.install").random() != derive_rng(8, "skill.install").random()


def test_same_stream_reproduces_sequence() -> None:
    first = [derive_rng(7, "s").random() for _ in range(5)]
    second = [derive_rng(7, "s").random() for _ in range(5)]
    assert first == second


def test_derived_streams_do_not_interfere() -> None:
    """先消费 install 流不应改变 carry 流的走向（流的独立性）。"""
    install = derive_rng(7, "skill.install")
    carry = derive_rng(7, "skill.carry")
    reference = derive_rng(7, "skill.carry")
    expected_carry = [reference.random() for _ in range(3)]
    for _ in range(10):
        install.random()
    assert [carry.random() for _ in range(3)] == expected_carry


def test_clock_is_monotonic() -> None:
    clock = VirtualClock()
    assert clock.time_s == 0.0
    clock.advance(12.5)
    assert clock.time_s == 12.5
    clock.advance(0)
    assert clock.time_s == 12.5
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_jsonl_roundtrip_with_chinese(tmp_path) -> None:
    records = [
        {"episode_id": "e1", "summary": "螺丝装好了", "value": 1.5},
        {"episode_id": "e2", "summary": "散落，需要人", "value": None},
    ]
    path = tmp_path / "out" / "episodes.jsonl"
    assert write_jsonl(path, records) == 2
    assert read_jsonl(path) == records
    raw = path.read_text(encoding="utf-8")
    assert "螺丝装好了" in raw
    assert "\\u" not in raw


def test_jsonl_append(tmp_path) -> None:
    path = tmp_path / "a.jsonl"
    append_jsonl(path, {"x": 1})
    append_jsonl(path, {"x": 2})
    assert read_jsonl(path) == [{"x": 1}, {"x": 2}]


def test_jsonl_dumps_is_sorted_and_compact() -> None:
    assert dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_jsonl_iter_and_missing_file(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, [{"x": 1}, {"x": 2}])
    assert [row["x"] for row in iter_jsonl(path)] == [1, 2]
    with pytest.raises(FileNotFoundError):
        read_jsonl(tmp_path / "nope.jsonl")
    with pytest.raises(FileNotFoundError):
        list(iter_jsonl(tmp_path / "nope.jsonl"))


def test_jsonl_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"x": 1}\n\n{"x": 2}\n', encoding="utf-8")
    assert read_jsonl(path) == [{"x": 1}, {"x": 2}]


def test_wilson_interval_known_values() -> None:
    """n=10, k=5 的 Wilson 区间应落在 (0.2366, 0.7634) 附近。"""
    lo, hi = wilson_interval(5, 10)
    assert math.isclose(lo, 0.236593, rel_tol=1e-4)
    assert math.isclose(hi, 0.763407, rel_tol=1e-4)


def test_wilson_interval_edge_cases() -> None:
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0 and 0.0 < hi < 0.5
    lo, hi = wilson_interval(10, 10)
    assert math.isclose(hi, 1.0) and 0.5 < lo < 1.0
    assert wilson_interval(0, 0) == (0.0, 0.0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_mean_and_stderr() -> None:
    mean, err = mean_and_stderr([1.0, 2.0, 3.0, 4.0])
    assert math.isclose(mean, 2.5)
    assert math.isclose(err, math.sqrt(5.0 / 3.0 / 4.0), rel_tol=1e-9)
    assert mean_and_stderr([]) == (0.0, 0.0)
