"""跨进程可复现（§C4）：不同 ``PYTHONHASHSEED`` 下，sweep 的产物必须逐字节一致。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import shelf_agent

#: 参与比对的产物。
ARTIFACTS = ("episodes.jsonl", "summary.csv")


def _src_dir() -> str:
    """当前导入的 shelf_agent 所在的 src 目录。"""
    return str(Path(shelf_agent.__file__).resolve().parents[1])


def _run_sweep(out_dir: Path, hash_seed: str, seeds: str = "10") -> dict[str, str]:
    """在独立进程里跑一次小 sweep，返回产物的 sha256。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env["PYTHONPATH"] = os.pathsep.join(
        [_src_dir(), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "shelf_agent.eval.cli",
            "sweep",
            "--seeds", seeds,
            "--failure-rates", "0,0.2",
            "--policies", "naive,rule_noisy",
            "--out", str(out_dir),
        ],
        env=env,
        capture_output=True,
        timeout=120,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"子进程失败（returncode={proc.returncode}）：\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return {
        name: hashlib.sha256((out_dir / name).read_bytes()).hexdigest() for name in ARTIFACTS
    }


def _first_difference(left: Path, right: Path) -> str:
    """两个产物的第一处差异（失败时直接指出是哪一行）。"""
    a = left.read_bytes().splitlines()
    b = right.read_bytes().splitlines()
    for index, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return (
                f"第 {index + 1} 行不同：\n"
                f"  a: {x[:400].decode('utf-8', 'replace')}\n"
                f"  b: {y[:400].decode('utf-8', 'replace')}"
            )
    if len(a) != len(b):
        return f"行数不同：{len(a)} vs {len(b)}"
    return ""


@pytest.mark.slow
def test_sweep_is_byte_identical_across_processes(tmp_path: Path) -> None:
    """PYTHONHASHSEED=1 与 =2：episodes.jsonl 与 summary.csv 的 sha256 必须相同。"""
    first = _run_sweep(tmp_path / "a", "1")
    second = _run_sweep(tmp_path / "b", "2")
    detail = ""
    for name in ARTIFACTS:
        if first[name] != second[name]:
            detail = f"{name}: {_first_difference(tmp_path / 'a' / name, tmp_path / 'b' / name)}"
            break
    assert first == second, f"跨进程产物不一致：{first} vs {second}\n{detail}"


@pytest.mark.slow
def test_same_process_same_seed_is_identical(tmp_path: Path) -> None:
    """同一 hash seed 下跑两次也必须一致（回归护栏，用更小的规模）。"""
    first = _run_sweep(tmp_path / "c", "7", seeds="3")
    second = _run_sweep(tmp_path / "d", "7", seeds="3")
    assert first == second
