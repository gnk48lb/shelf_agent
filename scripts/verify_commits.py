"""最终检查：跑全量测试 → 提交待提交的改动 → 逐 commit 验绿。

用法::

    uv run python scripts/verify_commits.py

产出 ``results/_commits_check.txt``。
注意：worktree 里跑 pytest 时必须把 worktree 的 src 放进 PYTHONPATH——项目是
editable 安装，否则 import 到的是主工作区，测的不是那个 commit。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "_commits_check.txt"
#: 要验证的 commit（默认为本轮的两个）。
REFS = ["80721c2", "26b744f"]

#: 待提交的这一小步（把 eval/report.py 拆到 400 行以内）。
PENDING = [
    "src/shelf_agent/eval/report.py",
    "src/shelf_agent/eval/summary.py",
    "src/shelf_agent/eval/sweep.py",
    "docs/assumptions.md",
    "scripts/verify_commits.py",
]

PENDING_MSG = """fix: eval/report.py 拆出 summary.py（单文件 ≤ 400 行）

- eval/summary.py 承接扫描结果的 markdown 渲染（summary.md / verifier_sweep.md）
- eval/report.py 只保留出图与时间线渲染，401 → 291 行
- assumptions.md 的 A3 补上 n_unnecessary_resets 的计数口径（按调用计）

Co-Authored-By: Claude Code <noreply@anthropic.com>
"""

_lines: list[str] = []


def emit(text: str = "") -> None:
    print(text)
    _lines.append(text)


def sh(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=str(cwd or ROOT), env=env, input=stdin, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    # ------------------------------------------------- 1) 全量测试（主工作区）
    emit("=" * 70)
    emit("1) 全量 pytest（主工作区）")
    emit("=" * 70)
    rc, out = sh([sys.executable, "-m", "pytest", "-q", "-o", "addopts="])
    emit(f"returncode={rc}")
    emit(out[-1500:])
    if rc != 0:
        emit("❌ 主工作区不绿，**不提交**任何东西。")
        OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        return 1

    # ------------------------------------------------------ 2) 提交待提交的改动
    changed = [p for p in PENDING if (ROOT / p).exists()]
    if changed:
        rc, out = sh(["git", "add", "--", *changed])
        emit(f"\ngit add -> {rc}")
        rc, out = sh(["git", "commit", "-F", "-"], stdin=PENDING_MSG)
        emit(f"git commit -> {rc}")
        emit(out[-800:])
        REFS.append("HEAD")

    # ------------------------------------------------ 3) 逐 commit 单独验绿
    for ref in REFS:
        emit("")
        emit("=" * 70)
        emit(f"验证 {ref}")
        emit("=" * 70)
        worktree = ROOT.parent / f"shelf_check_{ref}"
        sh(["git", "worktree", "remove", str(worktree), "--force"])
        rc, out = sh(["git", "worktree", "add", str(worktree), ref])
        emit(f"worktree add -> {rc}")
        if rc != 0:
            emit(out[-1500:])
            continue
        env = dict(os.environ)
        env["PYTHONPATH"] = str(worktree / "src")
        rc, out = sh(
            [sys.executable, "-m", "pytest", "-q", "-o", "addopts="],
            cwd=worktree, env=env,
        )
        emit(f"pytest -> returncode={rc}")
        emit(out[-1200:])
        emit(f"结论：{ref} 单独 checkout {'全绿' if rc == 0 else '有失败'}")
        sh(["git", "worktree", "remove", str(worktree), "--force"])

    emit("")
    emit("=" * 70)
    emit("最终状态")
    emit("=" * 70)
    sh(["git", "log", "--oneline", "-8"])
    sh(["git", "status", "--short"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
