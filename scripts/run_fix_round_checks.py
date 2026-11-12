"""一次性跑完 FIX_TASK 收尾需要的全部命令，并把结果写进文件。

用途：主会话的命令通道（安全分类器）不通时，由用户在终端跑这个脚本，
产出写到 ``results/_checks.txt``，主会话直接读那个文件即可。

用法::

    uv run python scripts/run_fix_round_checks.py

包含：
1. 全量 pytest（带汇总行）
2. 覆盖率汇总行
3. **红的证据**：在 HEAD（旧代码）的独立 worktree 里跑新测试，贴失败输出
4. DoD 两条 demo grep
5. PYTHONHASHSEED=1/2 的产物 sha256 比对
6. 未知策略名的退出码
7. 最终 100-seed sweep + verifier sweep（用于填 README §7）
8. 用真实产物渲染 README §7 的三张表
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "results" / "_checks.txt"
REPO = ROOT / "shelf_agent"

_lines: list[str] = []


def emit(text: str = "") -> None:
    print(text)
    _lines.append(text)


def sh(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    """跑一条命令，返回 (returncode, 输出)。"""
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


def section(title: str) -> None:
    emit("")
    emit("=" * 78)
    emit(title)
    emit("=" * 78)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 1 全量
    section("1) 全量 pytest")
    rc, out = sh(py("-m", "pytest", "-q", "-o", "addopts="))
    emit(f"returncode={rc}")
    emit(out[-4000:])

    # ------------------------------------------------------------- 2 覆盖率
    section("2) 覆盖率")
    rc, out = sh(py("-m", "pytest", "-q", "-o", "addopts=", "--cov=shelf_agent", "--cov-report=term"))
    emit(f"returncode={rc}")
    for line in out.splitlines():
        if "TOTAL" in line or line.strip().startswith(("src", "TOTAL")):
            emit(line)

    # ------------------------------------------------------------ 3 红的证据
    section("3) 红的证据：旧代码（HEAD）上跑新测试")
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
            dst = red_dir / "tests" / name
            if src.is_file():
                shutil.copy2(src, dst)
                emit(f"  拷入 {name}")
        # 关键：必须让 worktree 里的 src 优先于「已安装的可编辑包」，
        # 否则测的还是主工作区（已修好的代码），红不起来。
        red_env = dict(os.environ)
        red_env["PYTHONPATH"] = str(red_dir / "src")
        rc, out = sh(py("-m", "pytest", "-q", "-o", "addopts=", "tests/test_observation_invariance.py",
                        "tests/test_random_stream_purity.py", "tests/test_config_validation.py"),
                     cwd=red_dir, env=red_env)
        emit(f"旧代码上跑新测试 -> returncode={rc}")
        emit(out[-6000:])
        rc, out = sh(["git", "worktree", "remove", str(red_dir), "--force"], cwd=ROOT)
        emit(f"清理 worktree -> returncode={rc}")

    # ------------------------------------------------------------ 4 demo grep
    section("4) DoD：demo 的两条 grep")
    rc, out = sh(py("-m", "shelf_agent.eval.cli", "demo", "--scenario", "stray_needs_human", "--no-color"))
    count = out.count("掉在箱外")
    emit(f"grep -c 掉在箱外 = {count}   （必须是 0）")
    rc, out2 = sh(py("-m", "shelf_agent.eval.cli", "demo", "--scenario", "stray_needs_human",
                     "--show-truth", "--no-color"))
    injected = [line for line in out2.splitlines() if "注入" in line]
    emit(f"含「注入」的行数 = {len(injected)}   （必须 > 0）")
    for line in injected:
        emit(f"  {line}")

    # -------------------------------------------------- 5 PYTHONHASHSEED 比对
    section("5) DoD：PYTHONHASHSEED=1 与 =2 的产物比对")
    digests: dict[str, dict[str, str]] = {}
    for tag, seed in (("h1", "1"), ("h2", "2")):
        out_dir = ROOT / "results" / f"_hash_{tag}"
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(SRC)
        rc, out = sh(
            py("-m", "shelf_agent.eval.cli", "sweep", "--seeds", "20", "--failure-rates", "0,0.2",
               "--policies", "naive,rule_noisy", "--out", str(out_dir)),
            env=env,
        )
        emit(f"[{tag}] returncode={rc}")
        if rc != 0:
            emit(out[-2000:])
            continue
        digests[tag] = {
            name: hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
            for name in ("episodes.jsonl", "summary.csv")
        }
        emit(f"[{tag}] {digests[tag]}")
    if len(digests) == 2:
        same = digests["h1"] == digests["h2"]
        emit(f"两次产物是否逐字节相同：{same}   （必须 True）")
    else:
        emit("有子进程失败，无法比对")

    # ---------------------------------------------------------- 6 未知策略名
    section("6) DoD：未知策略名必须报错退出")
    rc, out = sh(py("-m", "shelf_agent.eval.cli", "sweep", "--policies", "rule_noysy",
                    "--seeds", "1", "--failure-rates", "0", "--out", str(ROOT / "results" / "_bad")))
    emit(f"returncode={rc}   （必须非 0）")
    emit(out[-800:])

    # ------------------------------------------------------------- 7 最终 sweep
    section("7) 最终 sweep（100 seed × 5 故障率 × 4 策略）")
    rc, out = sh(py("-m", "shelf_agent.eval.cli", "sweep", "--config", str(ROOT / "configs" / "real_screw.yaml"),
                    "--seeds", "100", "--failure-rates", "0,0.05,0.1,0.2,0.3",
                    "--policies", "naive,retry_only,rule_oracle,rule_noisy",
                    "--out", str(ROOT / "results")))
    emit(f"returncode={rc}")
    emit(out[-3000:])

    section("8) 验证器质量扫描（200 seed × 5 档位 × 3 策略）")
    rc, out = sh(py("-m", "shelf_agent.eval.cli", "sweep-verifier",
                    "--config", str(ROOT / "configs" / "real_screw.yaml"),
                    "--failure-rate", "0.3", "--levels", "0,0.1,0.25,0.5,1.0", "--seeds", "200",
                    "--policies", "rule_oracle,rule_noisy,retry_only",
                    "--out", str(ROOT / "results" / "verifier")))
    emit(f"returncode={rc}")
    emit(out[-3000:])

    section("9) 用真实产物渲染 README §7 的三张表")
    rc, out = sh(py(str(ROOT / "scripts" / "render_readme_tables.py"),
                    "--out", str(ROOT / "results"), "--readme", str(ROOT / "README.md")))
    emit(f"returncode={rc}")
    emit(out[-1500:])

    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\n全部结果已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
