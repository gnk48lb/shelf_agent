"""验证每个 commit 单独 checkout 是否全绿，并把结果写进文件。

用法::

    uv run python scripts/verify_commits.py

产出 ``results/_commits_check.txt``。
注意：必须把 worktree 的 src 放进 PYTHONPATH——项目是 editable 安装，
否则 import 到的是主工作区，测的不是那个 commit。
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

_lines: list[str] = []


def emit(text: str = "") -> None:
    print(text)
    _lines.append(text)


def sh(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=str(cwd or ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    # 把验证脚本自己也收进去
    helper = ROOT / "scripts" / "verify_commits.py"
    if helper.is_file():
        rc, out = sh(["git", "add", "--", "scripts/verify_commits.py"])
        emit(f"git add verify_commits.py -> {rc}")
        rc, out = sh(
            ["git", "commit", "-m", "chore: 补上逐 commit 验绿的脚本",
             "-m", "scripts/verify_commits.py 把每个 commit 单独 checkout 到独立 worktree 跑 pytest，"
                   "并把结果写进 results/_commits_check.txt。\n\n"
                   "Co-Authored-By: Claude Code <noreply@anthropic.com>"],
        )
        emit(f"git commit -> {rc}")
        emit(out[-1500:])

    for ref in REFS:
        emit("")
        emit("=" * 70)
        emit(f"验证 {ref}")
        emit("=" * 70)
        worktree = ROOT.parent / f"shelf_check_{ref}"
        rc, out = sh(["git", "worktree", "remove", str(worktree), "--force"])
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
        emit(out[-1500:])
        emit(f"结论：{ref} 单独 checkout {'全绿' if rc == 0 else '有失败'}")
        sh(["git", "worktree", "remove", str(worktree), "--force"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"\n结果已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
