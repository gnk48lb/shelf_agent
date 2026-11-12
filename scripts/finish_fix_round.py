"""补跑最后两件事：红证据（修正版）+ 重跑 sweep 并渲染 README §7。

用法::

    uv run python scripts/finish_fix_round.py

产出 ``results/_checks2.txt``（红证据的完整失败输出也写在里面）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "results" / "_checks2.txt"

_lines: list[str] = []


def emit(text: str = "") -> None:
    print(text)
    _lines.append(text)


def sh(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def py(*args: str) -> list[str]:
    return [sys.executable, *args]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="补跑红证据 / sweep / README 渲染")
    parser.add_argument("--only", default="all", choices=["all", "red"], help="只跑红证据")
    args = parser.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------ 1) 红的证据（修正版）
    emit("=" * 78)
    emit("1) 红的证据：旧代码（HEAD）上跑新测试")
    emit("=" * 78)
    red_dir = ROOT.parent / "shelf_red"
    if red_dir.exists():
        shutil.rmtree(red_dir, ignore_errors=True)
    rc, out = sh(["git", "worktree", "add", str(red_dir), "HEAD"])
    emit(f"git worktree add -> returncode={rc}")
    if rc == 0:
        for name in (
            "test_observation_invariance.py",
            "test_random_stream_purity.py",
            "test_config_validation.py",
        ):
            src = ROOT / "tests" / name
            if src.is_file():
                shutil.copy2(src, red_dir / "tests" / name)
                emit(f"  拷入 {name}")
        red_env = dict(os.environ)
        red_env["PYTHONPATH"] = str(red_dir / "src")
        # 三个文件**分开跑**：其中一个在旧代码上收集期就 ImportError，
        # 一起跑会让 pytest 直接中断，另外两个的失败信息看不到。
        for name in (
            "test_observation_invariance.py",
            "test_random_stream_purity.py",
            "test_config_validation.py",
        ):
            rc, out = sh(
                py("-m", "pytest", "-q", "-o", "addopts=", f"tests/{name}"),
                cwd=red_dir, env=red_env,
            )
            emit(f"--- {name} -> returncode={rc}（非 0 才是预期的「红」）")
            emit(out[-5000:])
        rc, _ = sh(["git", "worktree", "remove", str(red_dir), "--force"], cwd=ROOT)
        emit(f"清理 worktree -> returncode={rc}")

    # ------------------------------------------------------- 2) 重跑两个 sweep
    if args.only == "red":
        OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        print(f"\n结果已写入 {OUT}")
        return 0
    emit("")
    emit("=" * 78)
    emit("2) 最终 sweep（100 seed × 5 故障率 × 4 策略）")
    emit("=" * 78)
    rc, out = sh(py("-m", "shelf_agent.eval.cli", "sweep",
                    "--config", str(ROOT / "configs" / "real_screw.yaml"),
                    "--seeds", "100", "--failure-rates", "0,0.05,0.1,0.2,0.3",
                    "--policies", "naive,retry_only,rule_oracle,rule_noisy",
                    "--out", str(ROOT / "results")))
    emit(f"returncode={rc}")
    emit(out[-3000:])

    emit("")
    emit("=" * 78)
    emit("3) 验证器质量扫描（200 seed × 5 档位 × 3 策略）")
    emit("=" * 78)
    rc, out = sh(py("-m", "shelf_agent.eval.cli", "sweep-verifier",
                    "--config", str(ROOT / "configs" / "real_screw.yaml"),
                    "--failure-rate", "0.3", "--levels", "0,0.1,0.25,0.5,1.0", "--seeds", "200",
                    "--policies", "rule_oracle,rule_noisy,retry_only",
                    "--out", str(ROOT / "results" / "verifier")))
    emit(f"returncode={rc}")
    emit(out[-3000:])

    # ---------------------------------------------------------- 3) 渲染 README
    emit("")
    emit("=" * 78)
    emit("4) 用真实产物渲染 README §7 的三张表")
    emit("=" * 78)
    rc, out = sh(py(str(ROOT / "scripts" / "render_readme_tables.py"),
                    "--out", str(ROOT / "results"), "--readme", str(ROOT / "README.md")))
    emit(f"returncode={rc}")
    emit(out[-1500:])

    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
