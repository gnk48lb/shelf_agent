"""L0 配置层：YAML 加载、运行时覆盖、规范化哈希、LLM 环境变量读取。

本模块只依赖标准库，可以被任意层 import。工具契约的结构校验在
``contracts/spec.py``（L1）完成，这里只做「把文件读进来、变成可用对象」。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

#: 契约版本号，纳入 config_hash（A13）。
CONTRACT_VERSION = "1"

#: 各技能已知的故障类型（配置里的权重只能用这些键；拼错会被静默忽略）。
KNOWN_FAILURE_KINDS: dict[str, frozenset[str]] = {
    "robot1_install_screws": frozenset({"STUCK", "INCOMPLETE", "STRAY"}),
    "robot2_carry_box": frozenset({"TIMEOUT", "SILENT_FAIL"}),
}

#: 合法的验证器类型。
VERIFIER_KINDS = ("oracle", "noisy", "scripted")

#: 仓库根目录（src/shelf_agent/config.py 的上两级）。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 默认配置文件路径。
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "real_screw.yaml"


class ConfigError(ValueError):
    """配置文件缺字段、类型不对或路径无效。错误信息必须指出具体字段路径。"""


@dataclass(frozen=True)
class Duration:
    """技能耗时模型：均值 + 相对抖动。"""

    mean_s: float
    jitter: float = 0.0

    def sample(self, rng: Any) -> float:
        """按抖动比例采样一次耗时；抖动因子取自 [1-jitter, 1+jitter]。"""
        if self.jitter <= 0.0:
            return float(self.mean_s)
        factor = 1.0 + rng.uniform(-self.jitter, self.jitter)
        return float(self.mean_s) * factor


@dataclass(frozen=True)
class FailureSkillConfig:
    """单个技能的故障注入参数。``rate`` 为 None 表示沿用全局 rate。"""

    rate: float | None = None
    weights: Mapping[str, float] = field(default_factory=dict)

    def effective_rate(self, global_rate: float) -> float:
        return float(self.rate) if self.rate is not None else float(global_rate)


@dataclass(frozen=True)
class FailuresConfig:
    """全局故障旋钮与各技能权重。"""

    rate: float = 0.0
    skills: Mapping[str, FailureSkillConfig] = field(default_factory=dict)
    p_lost: float = 0.0

    def skill(self, name: str) -> FailureSkillConfig:
        return self.skills.get(name, FailureSkillConfig())


@dataclass(frozen=True)
class VerifierConfig:
    """验证器参数。``kind`` ∈ {oracle, noisy, scripted}。"""

    kind: str = "noisy"
    p_inside_correct: float = 0.95
    boundary_confusion: float = 0.10
    p_unclear: float = 0.03
    p_outside_miss: float = 0.05
    confusion_table_path: str | None = None
    scripted_reports: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class HumanConfig:
    """人类求助通道。"""

    latency_s: float = 120.0
    max_asks: int = 2


@dataclass(frozen=True)
class InspectorConfig:
    """巡检机器人。``mode`` ∈ {noop, shelf_status}。"""

    mode: str = "noop"
    false_negative: float = 0.0
    duration_s: float = 40.0


@dataclass(frozen=True)
class BudgetConfig:
    """两级预算与无效调用上限。"""

    soft_limit: int = 40
    hard_extra: int = 3
    max_invalid_calls: int = 10


@dataclass(frozen=True)
class RulePolicyConfig:
    """RuleSelfHealPolicy 的各项限额。"""

    max_install_calls: int = 6
    max_resets_per_robot: int = 3
    max_unclear_retries: int = 2
    max_carry_attempts: int = 3
    max_human_asks: int = 2


@dataclass(frozen=True)
class AppConfig:
    """一个场景配置的完整视图。``raw`` 保留原始 dict 供哈希与调试使用。"""

    scenario: str
    target_screws: int
    raw: Mapping[str, Any]
    failures: FailuresConfig
    verifier: VerifierConfig
    human: HumanConfig
    inspector: InspectorConfig
    budget: BudgetConfig
    rule_policy: RulePolicyConfig
    source_path: Path | None = None

    @property
    def tools_section(self) -> list[Mapping[str, Any]]:
        """契约里的工具原始列表（结构由 contracts.spec 校验）。"""
        return list(self.raw.get("tools", []))

    def with_overrides(self, overrides: Mapping[str, Any]) -> "AppConfig":
        """返回覆盖指定顶层字段后的新配置（不修改自身）。"""
        new_raw = dict(self.raw)
        new_raw.update(overrides)
        return replace(self, raw=new_raw)

    def config_hash(self, overrides: Mapping[str, Any] | None = None) -> str:
        """规范化配置的 sha256（A13：不含 seed 与 episode_id）。"""
        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "config": json.loads(json.dumps(self.raw, sort_keys=True, ensure_ascii=False)),
        }
        if overrides:
            payload["overrides"] = json.loads(
                json.dumps(overrides, sort_keys=True, ensure_ascii=False)
            )
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """稳定序列化：键排序、无多余空白、保留中文。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_yaml(path: str | Path) -> dict[str, Any]:
    """读取 YAML 文件并返回 dict。文件不存在或内容不是映射时抛 ConfigError。"""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"配置文件不存在：{p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        raise ConfigError(f"配置文件为空：{p}")
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射（dict），实际是 {type(data).__name__}：{p}")
    return data


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"配置段 '{name}' 必须是映射，实际是 {type(value).__name__}")
    return value


def _num(raw: Mapping[str, Any], name: str, default: float) -> float:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"字段 '{name}' 必须是数值，实际是 {type(value).__name__}")
    return float(value)


def _probability(raw: Mapping[str, Any], name: str, path: str, default: float) -> float:
    """概率字段：必须在 [0, 1] 内。"""
    value = _num(raw, name, default)
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"字段 '{path}' 必须是 [0, 1] 之间的概率，实际是 {value}")
    return value


def _at_least(raw: Mapping[str, Any], name: str, path: str, default: int, minimum: int) -> int:
    """整数字段：不得小于下界。"""
    value = _int(raw, name, default)
    if value < minimum:
        raise ConfigError(f"字段 '{path}' 必须 >= {minimum}，实际是 {value}")
    return value


def _int(raw: Mapping[str, Any], name: str, default: int) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"字段 '{name}' 必须是整数，实际是 {type(value).__name__}")
    return value


def _str(raw: Mapping[str, Any], name: str, default: str) -> str:
    value = raw.get(name, default)
    if not isinstance(value, str):
        raise ConfigError(f"字段 '{name}' 必须是字符串，实际是 {type(value).__name__}")
    return value


def _build_failures(raw: Mapping[str, Any]) -> FailuresConfig:
    section = _section(raw, "failures")
    rate = _probability(section, "rate", "failures.rate", 0.0)
    p_lost = _probability(section, "p_lost", "failures.p_lost", 0.0)
    skills: dict[str, FailureSkillConfig] = {}
    for name, spec in (section.get("skills") or {}).items():
        path = f"failures.skills.'{name}'"
        if not isinstance(spec, dict):
            raise ConfigError(f"{path} 必须是映射")
        skill_rate = spec.get("rate")
        if skill_rate is not None:
            if isinstance(skill_rate, bool) or not isinstance(skill_rate, (int, float)):
                raise ConfigError(f"{path}.rate 必须是数值或 null")
            if not 0.0 <= float(skill_rate) <= 1.0:
                raise ConfigError(
                    f"{path}.rate 必须是 [0, 1] 之间的概率，实际是 {skill_rate}"
                )
        weights = spec.get("weights") or {}
        if not isinstance(weights, dict):
            raise ConfigError(f"{path}.weights 必须是映射")
        known = KNOWN_FAILURE_KINDS.get(name)
        for key, w in weights.items():
            if isinstance(w, bool) or not isinstance(w, (int, float)):
                raise ConfigError(f"{path}.weights.'{key}' 必须是数值")
            if w < 0:
                raise ConfigError(f"{path}.weights.'{key}' 不能为负")
            if known is not None and key not in known:
                raise ConfigError(
                    f"{path}.weights 含未知故障类型 '{key}'，{name} 可用：{sorted(known)}"
                )
        if weights and sum(float(w) for w in weights.values()) <= 0:
            raise ConfigError(f"{path}.weights 的权重和必须 > 0")
        skills[name] = FailureSkillConfig(rate=skill_rate, weights=dict(weights))
    return FailuresConfig(rate=rate, skills=skills, p_lost=p_lost)


def _build_verifier(raw: Mapping[str, Any]) -> VerifierConfig:
    section = _section(raw, "verifier")
    kind = _str(section, "kind", "noisy")
    if kind not in VERIFIER_KINDS:
        raise ConfigError(
            f"字段 'verifier.kind' 必须是 {sorted(VERIFIER_KINDS)} 之一，实际是 '{kind}'"
        )
    reports = section.get("scripted_reports") or []
    if not isinstance(reports, list):
        raise ConfigError("verifier.scripted_reports 必须是列表")
    return VerifierConfig(
        kind=kind,
        p_inside_correct=_probability(section, "p_inside_correct", "verifier.p_inside_correct", 0.95),
        boundary_confusion=_probability(section, "boundary_confusion", "verifier.boundary_confusion", 0.10),
        p_unclear=_probability(section, "p_unclear", "verifier.p_unclear", 0.03),
        p_outside_miss=_probability(section, "p_outside_miss", "verifier.p_outside_miss", 0.05),
        confusion_table_path=section.get("confusion_table"),
        scripted_reports=list(reports),
    )


def build_config(raw: Mapping[str, Any], source_path: Path | None = None) -> AppConfig:
    """从已解析的 dict 构建 AppConfig（便于测试直接构造，不必写文件）。"""
    scenario = _str(raw, "scenario", "unnamed")
    target = _int(raw, "target_screws", 6)
    if target <= 0:
        raise ConfigError("字段 'target_screws' 必须为正整数")
    human_section = _section(raw, "human")
    inspector_section = _section(raw, "inspector")
    budget_section = _section(raw, "budget")
    rule_section = _section(raw, "rule_policy")
    inspector_mode = _str(inspector_section, "mode", "noop")
    if inspector_mode not in ("noop", "shelf_status"):
        raise ConfigError(
            f"字段 'inspector.mode' 必须是 'noop' 或 'shelf_status'，实际是 '{inspector_mode}'"
        )
    return AppConfig(
        scenario=scenario,
        target_screws=target,
        raw=dict(raw),
        failures=_build_failures(raw),
        verifier=_build_verifier(raw),
        human=HumanConfig(
            latency_s=_num(human_section, "latency_s", 120.0),
            max_asks=_int(human_section, "max_asks", 2),
        ),
        inspector=InspectorConfig(
            mode=inspector_mode,
            false_negative=_probability(
                inspector_section, "false_negative", "inspector.false_negative", 0.0
            ),
            duration_s=_num(inspector_section, "duration_s", 40.0),
        ),
        budget=BudgetConfig(
            soft_limit=_at_least(budget_section, "soft_limit", "budget.soft_limit", 40, 1),
            hard_extra=_at_least(budget_section, "hard_extra", "budget.hard_extra", 3, 0),
            max_invalid_calls=_at_least(
                budget_section, "max_invalid_calls", "budget.max_invalid_calls", 10, 1
            ),
        ),
        rule_policy=RulePolicyConfig(
            max_install_calls=_at_least(
                rule_section, "max_install_calls", "rule_policy.max_install_calls", 6, 0
            ),
            max_resets_per_robot=_at_least(
                rule_section, "max_resets_per_robot", "rule_policy.max_resets_per_robot", 3, 0
            ),
            max_unclear_retries=_at_least(
                rule_section, "max_unclear_retries", "rule_policy.max_unclear_retries", 2, 0
            ),
            max_carry_attempts=_at_least(
                rule_section, "max_carry_attempts", "rule_policy.max_carry_attempts", 3, 0
            ),
            max_human_asks=_at_least(
                rule_section, "max_human_asks", "rule_policy.max_human_asks", 2, 0
            ),
        ),
        source_path=source_path,
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """读取 YAML 配置文件并构建 AppConfig。"""
    p = Path(path)
    return build_config(load_yaml(p), source_path=p)


@dataclass(frozen=True)
class LLMSettings:
    """真实 LLM 的连接参数。值全部来自环境变量，代码里不写死地址。"""

    base_url: str
    model: str
    api_key: str

    def masked_key(self) -> str:
        """给日志用的脱敏 key，绝不返回明文。"""
        if not self.api_key:
            return "<empty>"
        return f"{self.api_key[:2]}***{self.api_key[-2:]}" if len(self.api_key) > 6 else "***"


ENV_BASE_URL = "SHELF_AGENT_LLM_BASE_URL"
ENV_MODEL = "SHELF_AGENT_LLM_MODEL"
ENV_API_KEY = "SHELF_AGENT_LLM_API_KEY"


def llm_settings_from_env(env: Mapping[str, str] | None = None) -> LLMSettings:
    """从环境变量读取 LLM 配置。缺任何一个都抛 ConfigError 并指出变量名（A17）。"""
    source = os.environ if env is None else env
    missing = [name for name in (ENV_BASE_URL, ENV_MODEL, ENV_API_KEY) if not source.get(name)]
    if missing:
        raise ConfigError(
            "缺少 LLM 环境变量：" + ", ".join(missing) + "。请先设置后再运行真实 LLM 模式。"
        )
    return LLMSettings(
        base_url=source[ENV_BASE_URL].rstrip("/"),
        model=source[ENV_MODEL],
        api_key=source[ENV_API_KEY],
    )
