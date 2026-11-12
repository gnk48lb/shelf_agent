"""验证器质量扫描（§C2）：噪声档位抬高时，误放行必须变多。"""

from __future__ import annotations

from pathlib import Path

import pytest

from shelf_agent.config import load_config
from shelf_agent.eval.sweep import run_verifier_sweep

CONFIG = load_config("configs/real_screw.yaml")


def _row(sweep, level: float, policy: str) -> dict:
    for row in sweep.summary_rows:
        if row["verifier_level"] == level and row["policy"] == policy:
            return row
    raise AssertionError(f"找不到 level={level} policy={policy} 的行")


def test_verifier_sweep_smoke(tmp_path: Path) -> None:
    sweep = run_verifier_sweep(
        CONFIG,
        failure_rate=0.3,
        levels=(0.0, 1.0),
        seeds=20,
        policies=("rule_oracle", "rule_noisy", "retry_only"),
        out_dir=tmp_path,
    )
    assert len(sweep.results) == 2 * 20 * 3
    for name in ("episodes.jsonl", "verifier_sweep.csv", "verifier_sweep.md", "verifier_sweep.png"):
        path = tmp_path / name
        assert path.is_file(), f"缺少 {name}"
        assert path.stat().st_size > 0


def test_oracle_never_false_finishes_at_any_level(tmp_path: Path) -> None:
    sweep = run_verifier_sweep(
        CONFIG,
        failure_rate=0.3,
        levels=(0.0, 0.5, 1.0),
        seeds=20,
        policies=("rule_oracle", "rule_noisy"),
        out_dir=tmp_path,
    )
    for level in (0.0, 0.5, 1.0):
        assert _row(sweep, level, "rule_oracle")["false_finish"] == 0


def test_noisy_false_finish_does_not_decrease_with_level(tmp_path: Path) -> None:
    sweep = run_verifier_sweep(
        CONFIG,
        failure_rate=0.3,
        levels=(0.0, 1.0),
        seeds=20,
        policies=("rule_noisy",),
        out_dir=tmp_path,
    )
    low = _row(sweep, 0.0, "rule_noisy")["false_finish"]
    high = _row(sweep, 1.0, "rule_noisy")["false_finish"]
    assert high >= low, f"档位抬高后误放行反而变少：{low} → {high}"


def test_verifier_level_changes_config_hash(tmp_path: Path) -> None:
    sweep = run_verifier_sweep(
        CONFIG,
        failure_rate=0.3,
        levels=(0.0, 1.0),
        seeds=5,
        policies=("rule_noisy",),
        out_dir=tmp_path,
    )
    hashes = {r.config_hash for r in sweep.results}
    assert len(hashes) == 2, "不同档位的 config_hash 必须不同"
