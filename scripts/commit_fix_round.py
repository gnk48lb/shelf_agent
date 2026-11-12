"""按阶段提交本轮改动（命令通道不通时的替身）。

用法::

    uv run python scripts/commit_fix_round.py

分两个 commit：
1. **Phase A + B + C** —— 观察通道泄漏、随机流纯度、配置校验、消融基线、
   验证器质量扫描（代码 + 对应测试）
2. **Phase D** —— 文档重写、英文工具描述与小清理

为什么是这个切法：第一版切法把 ``eval/runner.py``（需要 ``agents/retry_only.py``）
和 ``eval/cli.py``（需要 ``eval/sweep.py``）放进了第一个 commit，而被依赖的文件在第二个
——单独 checkout 第一个 commit 时直接 ModuleNotFoundError、7 个文件收集失败（实测）。
现在的切法保证**每个 commit 单独 checkout 都全绿**（脚本会逐个用独立 worktree 验证）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 基点 = Phase 3 + Phase 4 的 commit（**必须**是它：退到更早的基点会把
# Phase 3/4 的文件（llm_client / live_probe / tool_env / test_extended /
# test_llm_client）甩出历史，HEAD 就会缺文件）。
BASE = "c73cb1b"

#: HEAD 必须包含的文件（防止再把某个阶段的文件甩出历史）。
MUST_EXIST = [
    "src/shelf_agent/agents/llm_client.py",
    "src/shelf_agent/agents/react_agent.py",
    "src/shelf_agent/agents/retry_only.py",
    "src/shelf_agent/runtime/tool_env.py",
    "src/shelf_agent/perception/imagelib.py",
    "scripts/live_probe.py",
    "tests/test_extended.py",
    "tests/test_llm_client.py",
    "tests/test_react_agent.py",
    "tests/test_retry_only.py",
    "tests/test_observation_invariance.py",
]

MSG_1 = """Phase A + B + C: 观察通道泄漏、随机流纯度、配置校验、消融基线与验证器扫描

Phase A [必] 观察通道泄漏
- A1 install / carry 的 message 统一成常量（done / timeout / ok），文案不再泄露
  隐藏状态；BOX_NOT_AT_STATION 与 ask_human 的人类原话保留
- A2 真值通道记 debug_truth()["last_event"]（注入事件 / 前置违规），
  demo --show-truth 渲染成 [注入] STRAY / [前置违规] robot1=FAULT
- A3 新增 tests/test_observation_invariance.py：同一 status 下不同隐藏状态必须
  产生逐字相同的观测；附文案白名单与「策略不读 message」重放
- A4 test_no_leak.py / test_policies.py 引用新常量（不放宽既有断言）
- A5 README §3 与 assumptions.md A27（含取代 A25 旧做法）

Phase B [必]/[应]
- B1 随机流纯度：故障模型每次无条件抽 3 个、验证器每次无条件抽 5 个；
  验证器改用专用流 verifier:noise；utils/rng.py 写清流命名规则、删掉死常量
- B2 配置 fail-fast：概率 / verifier.kind / 权重键 / 限额在加载阶段校验并
  指出字段路径；策略名改用注册表 POLICIES，未知名字直接报错（CLI 先校验）
- B3 n_unnecessary_resets 改为按调用计（reset(all) 只有一台故障时不再计数）

Phase C [应]
- C1 新增 retry_only 消融基线：只看 status 的重试策略，永不验证 / 求助
- C2 新增 sweep-verifier：把验证器噪声档位当自变量（复用 run_sweep），
  产出 verifier_sweep.csv / .md / .png
- C3 出图 dodge：每策略固定 x 偏移，右图按策略配色、实线 FALSE_FINISH /
  虚线 unsafe_release；Wilson 误差棒夹到 0（小样本满分时上界低于点估计）
- C4 新增 tests/test_reproducibility_subprocess.py：PYTHONHASHSEED=1/2 下
  episodes.jsonl / summary.csv 的 sha256 必须相同

红证据（旧代码 HEAD 上跑新测试，实测）：
- tests/test_observation_invariance.py  10 failed / 10 passed，失败信息直接列出
  ('robot1_install_screws','done',None): ['最后一颗掉在箱外','补装完成','补装未达目标数']
- tests/test_random_stream_purity.py   失败：seed=0 于第 12/24 处 kind 序列分叉
- tests/test_config_validation.py     ImportError：旧代码没有 POLICIES

实测（100 seed × 5 故障率 × 4 策略）：
  naive 100/86/78/68/52，retry_only 100/93/89/81/73，rule_oracle / rule_noisy 100/100/100/100/99
验证器质量扫描（200 seed × 5 档位）：rule_noisy 的 FALSE_FINISH 0 → 1 → 2 → 12 → 21
  单调不降，rule_oracle 恒为 0。

Co-Authored-By: Claude Code <noreply@anthropic.com>
"""

MSG_2 = """Phase D: 文档重写、英文工具描述与小清理

- D1 README 用最终代码的真实运行重写 §7（含 retry_only 消融曲线、验证器质量扫描、
  配对比较）；删掉没有运行支撑的「换一组权重相对优势不会消失」；新增「组会演示」
  小节；docs 新增 A28–A30、Q14
- D2 小清理：无用 import、MAX_STEPS 合并、「连续两次」改用变量、
  LLM 侧去掉实验室名并改用 YAML 的 description_en（英文系统提示与 tools schema）、
  shelf_status 时 ReAct 暴露 inspector_run
- D3 [可] 转移表改名 FALLBACK_TRANSITIONS 并写清注释；未做「表即唯一来源」的
  重构（理由见 open_questions.md Q14）

验证：334 passed（8.3s），覆盖率 TOTAL 91%；DoD 四条全过
（demo grep 掉在箱外=0、[注入] STRAY 出现、PYTHONHASHSEED 1/2 产物逐字节相同、
未知策略名 exit 2）。每个 commit 单独 checkout 都用独立 worktree 验过。

Co-Authored-By: Claude Code <noreply@anthropic.com>
"""

GROUP_1 = [
    "src/shelf_agent/env/sim_backend.py",
    "src/shelf_agent/env/failures.py",
    "src/shelf_agent/perception/verifier.py",
    "src/shelf_agent/utils/rng.py",
    "src/shelf_agent/config.py",
    "src/shelf_agent/agents/rule_selfheal.py",
    "src/shelf_agent/agents/retry_only.py",
    "src/shelf_agent/eval/runner.py",
    "src/shelf_agent/eval/cli.py",
    "src/shelf_agent/eval/sweep.py",
    "src/shelf_agent/eval/report.py",
    "tests/test_observation_invariance.py",
    "tests/test_random_stream_purity.py",
    "tests/test_config_validation.py",
    "tests/test_retry_only.py",
    "tests/test_verifier_sweep.py",
    "tests/test_reproducibility_subprocess.py",
    "tests/test_sim_backend.py",
    "tests/test_failures.py",
    "tests/test_no_leak.py",
    "tests/test_policies.py",
    "tests/test_eval.py",
]

GROUP_2 = [
    "src/shelf_agent/contracts/spec.py",
    "src/shelf_agent/contracts/schema.py",
    "src/shelf_agent/agents/naive.py",
    "src/shelf_agent/agents/prompts.py",
    "src/shelf_agent/agents/react_agent.py",
    "src/shelf_agent/env/world.py",
    "src/shelf_agent/runtime/executor.py",
    "configs/real_screw.yaml",
    "configs/extended.yaml",
    "pyproject.toml",
    ".gitignore",
    "README.md",
    "docs/assumptions.md",
    "docs/open_questions.md",
    "FIX_TASK.md",
    "FIX_PROGRESS.md",
    "scripts/commit_fix_round.py",
    "scripts/diagnose_failures.py",
    "scripts/finish_fix_round.py",
    "scripts/render_readme_tables.py",
    "scripts/run_fix_round_checks.py",
    "scripts/verify_commits.py",
    "tests/test_react_agent.py",
    "tests/test_utils.py",
]


def run(cmd: list[str], stdin: str | None = None) -> int:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"$ {' '.join(cmd[:3])} ... -> {proc.returncode}")
    if proc.stdout.strip():
        print(proc.stdout[-2000:])
    if proc.stderr.strip():
        print(proc.stderr[-2000:])
    return proc.returncode


def verify_commit_green(ref: str = "HEAD") -> bool:
    """在独立 worktree 里跑一次 pytest，确认这个 commit 自己是绿的。

    注意：必须把 worktree 的 src 放进 PYTHONPATH，否则会 import 到主工作区
    （项目是 editable 安装），测的就不是这个 commit 了。
    """
    worktree = ROOT.parent / "shelf_verify"
    if worktree.exists():
        subprocess.run(["git", "worktree", "remove", str(worktree), "--force"], cwd=str(ROOT))
    rc = run(["git", "worktree", "add", str(worktree), ref])
    if rc != 0:
        return False
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts="],
        cwd=str(worktree), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    print(f"[{ref}] 独立 worktree 跑 pytest -> returncode={proc.returncode}")
    print((proc.stdout or "")[-1200:])
    subprocess.run(["git", "worktree", "remove", str(worktree), "--force"], cwd=str(ROOT))
    return proc.returncode == 0


def main() -> int:
    # 先把上一版切分（cb4b0f4 / bd88238 / b541edd）退回到 Phase 2 的基点，
    # 改动都还在工作区里，重新按修正后的分组提交。
    print(f"重置到 {BASE}（改动保留在工作区）")
    run(["git", "reset", "--soft", BASE])
    run(["git", "reset"])

    for group, message in ((GROUP_1, MSG_1), (GROUP_2, MSG_2)):
        missing = [p for p in group if not (ROOT / p).exists()]
        if missing:
            print(f"跳过（文件不存在）：{missing}")
            continue
        run(["git", "add", "--", *group])
        run(["git", "commit", "-F", "-"], stdin=message)
        ok = verify_commit_green()
        print(f"该 commit 单独 checkout 是否全绿：{ok}\n")
        if not ok:
            print("❌ 这个 commit 不绿，**不再继续提交后面的组**（避免把红的 commit 叠上去）。")
            break

    # 终检：HEAD 必须仍然包含各阶段的关键文件。
    rc, out = run(["git", "ls-files"])
    tracked = set(out.splitlines())
    lost = [p for p in MUST_EXIST if p not in tracked]
    print(f"HEAD 缺少的文件：{lost or '无'}")
    print("\n最终状态：")
    run(["git", "log", "--oneline", "-5"])
    run(["git", "status", "--short"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
