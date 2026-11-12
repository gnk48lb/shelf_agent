"""真机 + 联网 LLM 的调用流程模拟（默认离线、可复现）。

回答一个问题：``demo --scenario stuck_once`` 跑的是**规则策略 + 仿真后端 + 虚拟
时钟**，全程不联网；换成「真机 + 真实 LLM」之后，一次 episode 在线上到底发生
什么。

模拟分两层，**都走真实的库代码**：

* **LLM 层**：用真的 :class:`~shelf_agent.agents.llm_client.OpenAICompatClient`
  （同样的 URL、请求头、payload 结构、重试退避），只把 transport 换成本地的假
  网关。假网关把**逐字的实际请求体**打印出来，并按一段「像模像样的 LLM 决策」
  返回 tool call。``--real`` 则连真端点（需要 ``SHELF_AGENT_LLM_*`` 环境变量）。
* **设备层**：用真的 :class:`~shelf_agent.env.sim_backend.SimBackend` 跑语义，
  外面套一层 :class:`~live_sim.device.DeviceLink`——它把每次技能调用翻译成实验室
  API 的一次往返（契约里的 ``lab_name``），并在 ``--device-speed > 0`` 时按契约
  耗时真睡墙钟，展示真机上「虚拟时钟」如何被「真实时间」取代。

用法::

    # 默认：离线模拟（假网关 + 仿真设备），打印完整调用流程
    python scripts/live_flow_sim.py --scenario stuck_once

    # 模拟一个「不守规矩」的 LLM：不验证就搬运
    python scripts/live_flow_sim.py --scenario incomplete_topup --flaw skip-verify

    # 连真的 LLM（需先设 SHELF_AGENT_LLM_BASE_URL / _MODEL / _API_KEY）
    python scripts/live_flow_sim.py --scenario stuck_once --real

    # 模拟真机阻塞（2% 的真实耗时；120s 的阻塞会真的睡 2.4s）
    python scripts/live_flow_sim.py --scenario stuck_once --device-speed 0.02
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

# 让 `python scripts/live_flow_sim.py` 也能 import 到 src 里的包（未安装时可用）。
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)
# 同理，让脚本能 import 到同目录下的 live_sim 包。
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from live_sim import DeviceLink, FakeGateway, Printer  # noqa: E402
from live_sim.gateway import FLAWS  # noqa: E402
from shelf_agent.agents.llm_client import (  # noqa: E402
    OpenAICompatClient,
    TransportError,
    urllib_transport,
)
from shelf_agent.agents.react_agent import AgentError, ReActAgent  # noqa: E402
from shelf_agent.config import llm_settings_from_env, load_config  # noqa: E402
from shelf_agent.contracts.types import ToolCall  # noqa: E402
from shelf_agent.eval.runner import build_info, classify_outcome, collect_metrics  # noqa: E402
from shelf_agent.eval.scenarios import get_scenario  # noqa: E402
from shelf_agent.runtime.episode import make_episode  # noqa: E402

#: 假网关的 base URL（只用于让 URL 看起来像真的；不会发出任何请求）。
#: 客户端自己会拼上 ``/chat/completions``。
FAKE_BASE_URL = "http://127.0.0.1:3425/v1"

#: 真实端点的连接参数（``--real`` 时填进来）。
_REAL: dict[str, str] = {}


# ------------------------------------------------------------------- LLM 层


def build_transport(
    printer: Printer,
    gateway: FakeGateway | None,
    real: bool,
    flaky: int,
) -> Any:
    """造 transport：``real=True`` 时走真网络，否则走假网关。

    ``flaky > 0`` 时，第 ``flaky`` 次尝试抛一个可重试的 429——用来演示网关限流
    时客户端的指数退避。
    """

    def transport(
        payload: Mapping[str, Any],
        url: str,
        headers: Mapping[str, str],
    ) -> Mapping[str, Any]:
        printer.llm_request(payload, url, headers)
        try:
            if real:
                body = urllib_transport(payload, url, headers)
            else:
                body = gateway(payload, url, headers)  # type: ignore[misc]
        except TransportError as exc:
            printer.pending_status = exc.status
            raise
        printer.llm_response(body)
        return body

    if flaky and not real:
        base = transport
        attempts = {"n": 0}

        def flaky_transport(
            payload: Mapping[str, Any],
            url: str,
            headers: Mapping[str, str],
        ) -> Mapping[str, Any]:
            # 只在第 flaky 次尝试上失败一次——之后重试就成功（演示退避重试）。
            attempts["n"] += 1
            if attempts["n"] == flaky:
                printer.pending_status = 429
                raise TransportError("HTTP 429: rate limited", status=429, retryable=True)
            return base(payload, url, headers)

        return flaky_transport
    return transport


# ------------------------------------------------------------------- 主流程


def simulate(
    config: Any,
    scenario: Any,
    seed: int,
    printer: Printer,
    gateway: FakeGateway | None,
    real: bool,
    flaky: int,
    device_speed: float,
) -> dict[str, Any]:
    """跑一遍「真机 + LLM」的 episode，返回汇总。"""
    episode = make_episode(
        config,
        seed=seed,
        failure_script=scenario.failure_script,
        failure_rate=scenario.failure_rate,
        verifier_kind=scenario.verifier_kind,
        verifier_reports=scenario.verifier_reports,
    )
    device = DeviceLink(episode.backend, episode.spec, speed=device_speed, printer=printer)
    episode.executor.backend = device

    client = OpenAICompatClient(
        base_url=_REAL["base_url"] if real else FAKE_BASE_URL,
        model=_REAL["model"] if real else "qwen2.5-7b-instruct",
        api_key="sk-local-simulated-key",
        transport=build_transport(printer, gateway, real, flaky),
        sleep=printer.llm_retry,
        max_retries=3,
    )
    agent = ReActAgent(client=client)

    info = build_info(config, episode)
    agent.start(info)

    history: list[tuple[ToolCall, Any]] = []
    limit = config.budget.soft_limit + config.budget.hard_extra
    finish_call = None
    agent_error = False
    llm_network_error = False
    turn = 0
    while len(history) < limit:
        if episode.executor.finished or episode.budget.aborted:
            break
        turn += 1
        printer.turn(turn)
        try:
            call = agent.next_call(history)
        except AgentError as exc:
            agent_error = True
            print(f"\n✗ agent 中止：{exc}")
            break
        except TransportError as exc:
            # 真实部署里这就是「网关连不上」——整个 episode 作废（库里没有兜底）。
            agent_error = True
            llm_network_error = True
            print(f"\n✗ LLM 网络错误（重试用尽）：{exc}")
            break
        result = episode.executor.execute(call)
        printer.tool_result(result.to_dict())
        history.append((call, result))
        if call.name == "finish":
            finish_call = call
            break

    outcome = classify_outcome(
        finish_call=finish_call,
        truth_ok=_truth_ok(episode, config),
        budget_aborted=episode.budget.aborted,
        max_steps_reached=len(history) >= limit and finish_call is None,
        agent_error=agent_error,
    )
    metrics = collect_metrics(episode, config)
    return {
        "outcome": outcome,
        "metrics": metrics,
        "steps": episode.trajectory.to_list(),
        "n_llm_calls": printer.n_requests,
        "n_tool_calls": len(history),
        "llm_bytes": printer.bytes_sent,
        "llm_retries": printer.retries,
        "llm_network_error": llm_network_error,
        "device_wall_s": round(device.wall_s, 3),
        "device_hops": device.hops,
        "messages": agent.messages,
        "tools_schema": agent.tools_schema,
    }


def _truth_ok(episode: Any, config: Any) -> bool:
    """真值判据（评测用，agent 看不到）。"""
    from shelf_agent.perception.completion import truth_success

    box = episode.world.box(episode.box_id)
    return truth_success(box.location.value, box.inside, box.outside, config.target_screws)


# --------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真机 + 联网 LLM 的调用流程模拟")
    parser.add_argument("--scenario", default="stuck_once", help="场景名（默认 stuck_once）")
    parser.add_argument("--config", default="configs/real_screw.yaml")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--flaw", choices=FLAWS, default="none", help="模拟 LLM 的哪种失误")
    parser.add_argument(
        "--device-speed",
        type=float,
        default=0.0,
        help="设备耗时倍率：0=不睡，0.02 表示按契约耗时的 2%% 真睡墙钟",
    )
    parser.add_argument(
        "--flaky",
        type=int,
        default=0,
        help="第 N 次 LLM 请求返回 429（演示退避重试）",
    )
    parser.add_argument("--real", action="store_true", help="连真的 LLM 端点")
    parser.add_argument("--out", default="results/live_flow_sim")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    scenario = get_scenario(args.scenario)

    printer = Printer()
    printer.header(
        f"真机 + LLM 调用流程模拟 —— 场景 {scenario.name}（{scenario.description}）"
    )
    print(f"  契约      : {args.config}   目标螺丝数 = {config.target_screws}")
    print(f"  故障脚本  : {dict(scenario.failure_script or {})}")
    print(f"  LLM 剧本  : flaw={args.flaw}（none = 一个称职的 LLM）")

    if args.real:
        try:
            settings = llm_settings_from_env()
        except Exception as exc:  # noqa: BLE001 - 顶层要把错误说清楚
            print(str(exc), file=sys.stderr)
            return 2
        _REAL["base_url"] = settings.base_url
        _REAL["model"] = settings.model
        print(
            f"  真实端点  : {settings.base_url}  model={settings.model}  "
            f"key={settings.masked_key()}"
        )
    else:
        print(f"  假网关    : {FAKE_BASE_URL} → {FAKE_BASE_URL}/chat/completions（离线，不发任何请求）")

    gateway = FakeGateway(target=config.target_screws, flaw=args.flaw)
    summary = simulate(
        config=config,
        scenario=scenario,
        seed=args.seed,
        printer=printer,
        gateway=gateway,
        real=args.real,
        flaky=args.flaky,
        device_speed=args.device_speed,
    )

    # ---------------------------------------------------------------- 汇总
    printer.header("汇总")
    metrics = summary["metrics"]
    print(
        f"  LLM 往返        : {summary['n_llm_calls']} 次"
        f"（累计请求体 {summary['llm_bytes'] / 1024:.1f} KB）"
    )
    print(f"  LLM 重试        : {summary['llm_retries']} 次")
    if summary["llm_network_error"]:
        print("  LLM 网络        : 重试用尽 → episode 作废")
    print(f"  工具调用        : {summary['n_tool_calls']} 次")
    print(f"  设备墙钟        : {summary['device_wall_s']:.2f} s（真实时间）")
    print(f"  设备虚拟耗时    : {metrics['makespan_s']:.1f} s（契约耗时之和）")
    print(f"  timeout / reset : {metrics['n_timeouts']} / {metrics['n_resets']}")
    print(f"  unsafe_release  : {metrics['unsafe_release']}")
    print(f"  outcome         : {summary['outcome']}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario": args.scenario,
        "flaw": args.flaw,
        "real_llm": bool(args.real),
        "outcome": summary["outcome"],
        "metrics": metrics,
        "llm_calls": summary["n_llm_calls"],
        "llm_bytes": summary["llm_bytes"],
        "llm_retries": summary["llm_retries"],
        "llm_network_error": summary["llm_network_error"],
        "device_hops": summary["device_hops"],
        "steps": summary["steps"],
        "messages": summary["messages"],
        "tools_schema": summary["tools_schema"],
    }
    out_path = out_dir / f"{args.scenario}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  轨迹已写入      : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
