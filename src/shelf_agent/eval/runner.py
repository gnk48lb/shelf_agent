"""Episode 驱动与结果记录。

一个 episode 的流程：装配世界 → 策略开局 → 反复「问策略要一次调用 → 执行」
→ 按真值判定 outcome → 打包成一条可落盘的记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..config import AppConfig
from ..contracts.types import EpisodeInfo, ToolCall, ToolResult
from ..agents.naive import NaiveScriptPolicy
from ..agents.policy import Policy
from ..agents.rule_selfheal import RuleSelfHealPolicy
from ..perception.completion import truth_success
from .scenarios import Episode, make_episode

#: 最大步数：软预算 + 硬预算的额外次数（A11）。
MAX_STEPS_FALLBACK = 43


@dataclass
class EpisodeResult:
    """一个 episode 的完整结果。"""

    episode_id: str
    seed: int
    policy: str
    outcome: str
    metrics: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    truth_trace: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] | None = None
    tools_schema: list[dict[str, Any]] | None = None
    config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "episode_id": self.episode_id,
            "seed": self.seed,
            "config_hash": self.config_hash,
            "policy": self.policy,
            "outcome": self.outcome,
            "metrics": self.metrics,
            "steps": self.steps,
            "truth_trace": self.truth_trace,
        }
        if self.messages is not None:
            payload["messages"] = self.messages
        if self.tools_schema is not None:
            payload["tools_schema"] = self.tools_schema
        return payload


def make_policy(name: str, config: AppConfig) -> Policy:
    """按名字造策略。"""
    if name == "naive":
        return NaiveScriptPolicy()
    if name.startswith("rule"):
        limits = {
            "max_install_calls": config.rule_policy.max_install_calls,
            "max_resets_per_robot": config.rule_policy.max_resets_per_robot,
            "max_unclear_retries": config.rule_policy.max_unclear_retries,
            "max_carry_attempts": config.rule_policy.max_carry_attempts,
            "max_human_asks": config.rule_policy.max_human_asks,
        }
        return RuleSelfHealPolicy(limits=limits)
    raise ValueError(f"未知策略 '{name}'，可用：naive, rule")


def verifier_kind_for(policy_name: str) -> str:
    """策略名决定用哪个验证器。"""
    if policy_name.endswith("_noisy"):
        return "noisy"
    return "oracle"


def build_info(config: AppConfig, episode: Episode) -> EpisodeInfo:
    """给策略的只读环境说明（不含任何真值）。"""
    limits = {
        "max_install_calls": config.rule_policy.max_install_calls,
        "max_resets_per_robot": config.rule_policy.max_resets_per_robot,
        "max_unclear_retries": config.rule_policy.max_unclear_retries,
        "max_carry_attempts": config.rule_policy.max_carry_attempts,
        "max_human_asks": config.rule_policy.max_human_asks,
    }
    return EpisodeInfo(
        scenario=config.scenario,
        target_screws=config.target_screws,
        tools=tuple(episode.spec),
        limits=limits,
        inspector_mode=config.inspector.mode,
    )


def run_episode(
    config: AppConfig,
    seed: int,
    policy_name: str = "rule",
    policy: Policy | None = None,
    failure_script: Mapping[str, Sequence[Any]] | None = None,
    failure_rate: float | None = None,
    verifier_kind: str | None = None,
    verifier_reports: Sequence[Mapping[str, Any]] | None = None,
    max_steps: int | None = None,
    episode_id: str | None = None,
    hash_overrides: Mapping[str, Any] | None = None,
    on_step: Callable[[ToolCall, ToolResult], None] | None = None,
) -> EpisodeResult:
    """跑一个 episode 并返回结果。"""
    chosen = policy if policy is not None else make_policy(policy_name, config)
    kind = verifier_kind or verifier_kind_for(policy_name)
    episode = make_episode(
        config,
        seed=seed,
        failure_script=failure_script,
        failure_rate=failure_rate,
        verifier_kind=kind,
        verifier_reports=verifier_reports,
    )
    info = build_info(config, episode)
    chosen.start(info)
    history: list[tuple[ToolCall, ToolResult]] = []
    limit = max_steps if max_steps is not None else config.budget.soft_limit + config.budget.hard_extra
    finish_call: ToolCall | None = None
    while len(history) < limit:
        if episode.executor.finished or episode.budget.aborted:
            break
        call = chosen.next_call(history)
        result = episode.executor.execute(call)
        history.append((call, result))
        if on_step is not None:
            on_step(call, result)
        if call.name == "finish":
            finish_call = call
            break
    outcome = classify_outcome(
        finish_call=finish_call,
        truth_ok=_truth_ok(episode, config),
        budget_aborted=episode.budget.aborted,
        max_steps_reached=len(history) >= limit and finish_call is None,
        agent_error=False,
    )
    metrics = collect_metrics(episode, config)
    return EpisodeResult(
        episode_id=episode_id or f"{policy_name}-seed{seed}",
        seed=seed,
        policy=policy_name,
        outcome=outcome,
        metrics=metrics,
        steps=episode.trajectory.to_list(),
        truth_trace=[dict(step.truth_snapshot) for step in episode.trajectory.steps],
        config_hash=config.config_hash(hash_overrides),
    )


def _truth_ok(episode: Episode, config: AppConfig) -> bool:
    """真值判据：箱子在货架上且箱内正好 N 颗、箱外无散落。"""
    box = episode.world.box(episode.box_id)
    return truth_success(box.location.value, box.inside, box.outside, config.target_screws)


def collect_metrics(episode: Episode, config: AppConfig) -> dict[str, Any]:
    """汇总一个 episode 的指标。"""
    backend = episode.backend.metrics
    counts = episode.trajectory.tool_counts()
    metrics: dict[str, Any] = {
        "makespan_s": round(episode.clock.time_s, 6),
        "n_steps": len(episode.trajectory.steps),
        "n_timeouts": backend.n_timeouts,
        "n_resets": backend.n_resets,
        "n_unnecessary_resets": backend.n_unnecessary_resets,
        "n_wasted_calls": backend.n_wasted_calls,
        "n_human_asks": episode.world.human_asks,
        "n_invalid_calls": episode.budget.invalid_calls,
        "n_budget_rejections": episode.budget.rejected_calls,
        "unsafe_release": backend.unsafe_release,
    }
    for name in ("robot1_install_screws", "verify_box", "robot2_carry_box", "reset", "ask_human"):
        metrics[f"calls_{name}"] = counts.get(name, 0)
    return metrics


def classify_outcome(
    finish_call: ToolCall | None,
    truth_ok: bool,
    budget_aborted: bool = False,
    max_steps_reached: bool = False,
    agent_error: bool = False,
) -> str:
    """按 §5 的表格判定 outcome。"""
    if agent_error:
        return "AGENT_ERROR"
    if finish_call is None:
        if budget_aborted:
            return "ABORTED_BUDGET"
        if max_steps_reached:
            return "MAX_STEPS"
        return "MAX_STEPS"
    success = bool(finish_call.args.get("success"))
    if success and truth_ok:
        return "SUCCESS"
    if success and not truth_ok:
        return "FALSE_FINISH"
    if not success and truth_ok:
        return "MISSED_SUCCESS"
    return "SAFE_ABORT"


def run_react_episode(
    config: AppConfig,
    seed: int,
    agent: Any,
    failure_script: Mapping[str, Sequence[Any]] | None = None,
    failure_rate: float | None = None,
    verifier_kind: str | None = "oracle",
    verifier_reports: Sequence[Mapping[str, Any]] | None = None,
    max_steps: int | None = None,
    episode_id: str | None = None,
) -> EpisodeResult:
    """用 ReAct agent 跑一个 episode。

    与 :func:`run_episode` 的区别：agent 可能抛 :class:`AgentError`（连续无有效
    tool call），此时 outcome 记 ``AGENT_ERROR``；同时把完整 messages 与 tools
    schema 收进结果，供将来的 SFT 使用。
    """
    from ..agents.react_agent import AgentError

    episode = make_episode(
        config,
        seed=seed,
        failure_script=failure_script,
        failure_rate=failure_rate,
        verifier_kind=verifier_kind,
        verifier_reports=verifier_reports,
    )
    info = build_info(config, episode)
    agent.start(info)
    history: list[tuple[ToolCall, ToolResult]] = []
    limit = max_steps if max_steps is not None else config.budget.soft_limit + config.budget.hard_extra
    finish_call: ToolCall | None = None
    agent_error = False
    while len(history) < limit:
        if episode.executor.finished or episode.budget.aborted:
            break
        try:
            call = agent.next_call(history)
        except AgentError:
            agent_error = True
            break
        result = episode.executor.execute(call)
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
    return EpisodeResult(
        episode_id=episode_id or f"react-seed{seed}",
        seed=seed,
        policy=getattr(agent, "name", "react"),
        outcome=outcome,
        metrics=collect_metrics(episode, config),
        steps=episode.trajectory.to_list(),
        truth_trace=[dict(step.truth_snapshot) for step in episode.trajectory.steps],
        messages=getattr(agent, "messages", None),
        tools_schema=getattr(agent, "tools_schema", None),
        config_hash=config.config_hash(),
    )
