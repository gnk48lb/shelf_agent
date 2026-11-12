"""复现三个失败用例，把详细诊断写进文件（命令通道不通时也能读）。

用法::

    uv run python scripts/diagnose_failures.py

产出：``results/_diag.txt``（stdout 也打一份）。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shelf_agent.agents.rule_selfheal import RuleSelfHealPolicy  # noqa: E402
from shelf_agent.config import build_config, load_config  # noqa: E402
from shelf_agent.contracts.types import ToolCall, ToolResult  # noqa: E402
from shelf_agent.eval.runner import build_info, run_episode  # noqa: E402
from shelf_agent.eval.scenarios import SCENARIOS, make_episode  # noqa: E402

CONFIG = load_config("configs/real_screw.yaml")
OUT = Path("results/_diag.txt")


def _line(text: str = "") -> None:
    print(text)


_buffer: list[str] = []


def emit(text: str = "") -> None:
    _buffer.append(text)
    _line(text)


# ---------------------------------------------------------------- 诊断 1


def diag_policy_sequence() -> None:
    emit("=" * 70)
    emit("诊断 1：test_policy_sequence_does_not_depend_on_message")
    emit("=" * 70)
    for name in ("happy", "stuck_once"):
        scenario = SCENARIOS[name]
        emit(f"\n--- 场景 {name} ---")
        try:
            ep = make_episode(
                CONFIG,
                seed=0,
                failure_script=scenario.failure_script,
                failure_rate=scenario.failure_rate,
                verifier_kind=scenario.verifier_kind,
                verifier_reports=scenario.verifier_reports,
            )
            info = build_info(CONFIG, ep)
            policy = RuleSelfHealPolicy()
            policy.start(info)
            calls: list[ToolCall] = []
            results: list[ToolResult] = []
            history: list[tuple[ToolCall, ToolResult]] = []
            for _ in range(60):
                call = policy.next_call(history)
                result = ep.executor.execute(call)
                calls.append(call)
                results.append(result)
                history.append((call, result))
                if call.name == "finish":
                    break
            emit(f"原始序列（{len(calls)} 步）：")
            for i, (call, result) in enumerate(zip(calls, results)):
                emit(f"  {i}: {call.name}{call.args} -> {result.status} msg={result.message!r}")
            blanked = [
                (call, replace(result, message="")) for call, result in zip(calls, results)
            ]
            ep2 = make_episode(
                CONFIG,
                seed=0,
                failure_script=scenario.failure_script,
                failure_rate=scenario.failure_rate,
                verifier_kind=scenario.verifier_kind,
                verifier_reports=scenario.verifier_reports,
            )
            info2 = build_info(CONFIG, ep2)
            policy2 = RuleSelfHealPolicy()
            policy2.start(info2)
            emit("重放序列：")
            for index in range(len(blanked)):
                call2 = policy2.next_call(blanked[:index])
                emit(f"  {index}: {call2.name}{call2.args}")
            expected = [(c.name, dict(c.args)) for c in calls]
            got = []
            policy3 = RuleSelfHealPolicy()
            policy3.start(info2)
            for index in range(len(blanked)):
                c = policy3.next_call(blanked[:index])
                got.append((c.name, dict(c.args)))
            emit(f"相等？{expected == got}")
            if expected != got:
                emit(f"期望：{expected}")
                emit(f"实际：{got}")
        except Exception:  # noqa: BLE001 - 诊断脚本就是要打印异常
            emit("异常：")
            emit(traceback.format_exc())


# ---------------------------------------------------------------- 诊断 2


def diag_budget() -> None:
    emit("")
    emit("=" * 70)
    emit("诊断 2：test_budget_rejection_during_install_ends_honestly")
    emit("=" * 70)
    try:
        raw = {
            **CONFIG.raw,
            "budget": {**CONFIG.raw["budget"], "soft_limit": 1, "hard_extra": 2},
        }
        strict = build_config(raw)
        result = run_episode(
            strict, seed=0, policy_name="rule_oracle", failure_rate=0.0, verifier_kind="oracle"
        )
        emit(f"outcome = {result.outcome}")
        for i, step in enumerate(result.steps):
            emit(
                f"  {i}: {step['call']['name']}{step['call']['args']} -> "
                f"{step['result']['status']} {step['result'].get('error_code')} "
                f"msg={step['result'].get('message')!r}"
            )
        emit(f"steps[-1].call.args = {result.steps[-1]['call']['args']}")
    except Exception:  # noqa: BLE001
        emit("异常：")
        emit(traceback.format_exc())


# ---------------------------------------------------------------- 诊断 3


def diag_subprocess() -> None:
    emit("")
    emit("=" * 70)
    emit("诊断 3：test_sweep_is_byte_identical_across_processes")
    emit("=" * 70)
    base = Path("results/_diag_sweep")
    hashes: dict[str, dict[str, str]] = {}
    for tag, seed in (("h1", "1"), ("h2", "2")):
        out = base / tag
        out.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(
            [
                sys.executable, "-m", "shelf_agent.eval.cli", "sweep",
                "--seeds", "10", "--failure-rates", "0,0.2",
                "--policies", "naive,rule_noisy", "--out", str(out),
            ],
            env=env, capture_output=True, text=True, timeout=300,
        )
        emit(f"[{tag}] returncode={proc.returncode}")
        if proc.returncode != 0:
            emit(proc.stdout[-3000:])
            emit(proc.stderr[-3000:])
            continue
        hashes[tag] = {
            name: hashlib.sha256((out / name).read_bytes()).hexdigest()
            for name in ("episodes.jsonl", "summary.csv")
            if (out / name).is_file()
        }
    emit(f"hashes = {hashes}")
    if len(hashes) == 2:
        for name in ("episodes.jsonl", "summary.csv"):
            left = (base / "h1" / name).read_bytes().splitlines()
            right = (base / "h2" / name).read_bytes().splitlines()
            if left == right:
                continue
            emit(f"{name} 不同：")
            for i, (x, y) in enumerate(zip(left, right)):
                if x != y:
                    emit(f"  第 {i + 1} 行：")
                    emit(f"    h1: {x[:600].decode('utf-8', 'replace')}")
                    emit(f"    h2: {y[:600].decode('utf-8', 'replace')}")
                    break
            else:
                emit(f"  行数不同：{len(left)} vs {len(right)}")


def main() -> int:
    diag_policy_sequence()
    diag_budget()
    diag_subprocess()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_buffer) + "\n", encoding="utf-8")
    print(f"\n诊断已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
