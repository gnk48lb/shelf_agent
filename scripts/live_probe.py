"""真实 LLM 探针（**默认不联网**）。

这个脚本与离线 pytest 完全分离：除非显式打开开关，否则它只打印用法就退出。

用法::

    # 1) 先看看网关上有哪些模型（只读，仍需开关）
    python scripts/live_probe.py --list-models

    # 2) 跑一个 episode（需要先设好三个环境变量）
    set SHELF_AGENT_LLM_BASE_URL=http://127.0.0.1:3425/v1
    set SHELF_AGENT_LLM_MODEL=<上一步列出的模型 id>
    set SHELF_AGENT_LLM_API_KEY=local
    set SHELF_AGENT_LIVE_PROBE=1
    python scripts/live_probe.py --scenario happy

开关：环境变量 ``SHELF_AGENT_LIVE_PROBE=1``。**不要**在 CI 里打开它。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 让 `python scripts/live_probe.py` 也能 import 到 src 里的包（未安装时可用）。
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from shelf_agent.config import llm_settings_from_env  # noqa: E402
from shelf_agent.eval.runner import run_react_episode  # noqa: E402
from shelf_agent.eval.scenarios import SCENARIOS, get_scenario  # noqa: E402

#: 显式开关的环境变量名。
SWITCH_ENV = "SHELF_AGENT_LIVE_PROBE"


def _enabled() -> bool:
    return os.environ.get(SWITCH_ENV, "").strip() in {"1", "true", "yes", "on"}


def _list_models(base_url: str, api_key: str, timeout: float = 10.0) -> list[dict]:
    """GET {base_url}/models，列出可用模型。"""
    url = f"{base_url.rstrip('/')}/models"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("data") or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实 LLM 探针")
    parser.add_argument("--scenario", default="happy", help="要跑的场景名")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--list-models", action="store_true", help="只列模型然后退出")
    parser.add_argument("--config", default="configs/real_screw.yaml")
    parser.add_argument("--out", default="results/live_probe")
    args = parser.parse_args(argv)

    if not _enabled():
        print(
            f"探针默认关闭。要访问真实网关，请先设置环境变量 {SWITCH_ENV}=1。\n"
            "（离线测试与 CI 请勿打开。）"
        )
        return 2

    try:
        settings = llm_settings_from_env()
    except Exception as exc:  # noqa: BLE001 - 顶层需要说清楚
        print(str(exc), file=sys.stderr)
        return 2

    print(f"base_url = {settings.base_url}")
    print(f"model    = {settings.model}")
    print(f"api_key  = {settings.masked_key()}")

    if args.list_models:
        try:
            for item in _list_models(settings.base_url, settings.api_key):
                print(f"  - {item.get('id')}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"列模型失败：{exc}", file=sys.stderr)
            return 1
        return 0

    from shelf_agent.agents.react_agent import ReActAgent
    from shelf_agent.config import load_config
    from shelf_agent.eval.report import render_timeline
    from shelf_agent.utils.jsonl import write_jsonl

    config = load_config(args.config)
    scenario = get_scenario(args.scenario)
    agent = ReActAgent.from_settings(settings)
    result = run_react_episode(
        config,
        seed=args.seed,
        agent=agent,
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
        episode_id=f"live-{scenario.name}",
    )
    print(render_timeline(result.steps, result.outcome, show_truth=False))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "episodes.jsonl", [result.to_dict()])
    print(f"轨迹已写入 {out_dir / 'episodes.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
