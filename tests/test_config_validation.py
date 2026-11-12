"""配置与名字校验（§B2）：坏配置必须在**加载阶段**就报错，并指出字段路径。"""

from __future__ import annotations

import copy

import pytest

from shelf_agent.config import ConfigError, build_config, load_config
from shelf_agent.eval.cli import main
from shelf_agent.eval.runner import POLICIES, make_policy, verifier_kind_for

CONFIG = load_config("configs/real_screw.yaml")


def _raw() -> dict:
    return copy.deepcopy(CONFIG.raw)


def _with(path: str, value) -> dict:
    """按 '段.键' 路径改一个字段值。"""
    raw = _raw()
    parts = path.split(".")
    section = raw[parts[0]]
    for key in parts[1:-1]:
        section = section[key]
    section[parts[-1]] = value
    return raw


BAD_CONFIGS = [
    ("failures.rate", 1.5, "failures.rate"),
    ("failures.rate", -0.2, "failures.rate"),
    ("failures.p_lost", 2.0, "failures.p_lost"),
    ("verifier.kind", "orcle", "verifier.kind"),
    ("verifier.p_inside_correct", 3.0, "verifier.p_inside_correct"),
    ("verifier.boundary_confusion", -1.0, "verifier.boundary_confusion"),
    ("verifier.p_unclear", 1.5, "verifier.p_unclear"),
    ("verifier.p_outside_miss", 4.0, "verifier.p_outside_miss"),
    ("inspector.false_negative", 1.2, "inspector.false_negative"),
    ("budget.soft_limit", 0, "budget.soft_limit"),
    ("budget.hard_extra", -1, "budget.hard_extra"),
    ("budget.max_invalid_calls", 0, "budget.max_invalid_calls"),
    ("rule_policy.max_install_calls", -1, "rule_policy.max_install_calls"),
    ("rule_policy.max_resets_per_robot", -2, "rule_policy.max_resets_per_robot"),
    ("rule_policy.max_unclear_retries", -1, "rule_policy.max_unclear_retries"),
    ("rule_policy.max_carry_attempts", -1, "rule_policy.max_carry_attempts"),
    ("rule_policy.max_human_asks", -1, "rule_policy.max_human_asks"),
]


@pytest.mark.parametrize("path,value,expected", BAD_CONFIGS)
def test_bad_config_is_rejected_at_load_time(path: str, value, expected: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        build_config(_with(path, value))
    assert expected in str(excinfo.value), f"错误信息没有指出字段路径：{excinfo.value}"


def test_unknown_failure_kind_in_weights_is_rejected() -> None:
    raw = _raw()
    raw["failures"]["skills"]["robot1_install_screws"]["weights"]["STUK"] = 0.5
    with pytest.raises(ConfigError, match="STUK"):
        build_config(raw)


def test_zero_weight_sum_is_rejected() -> None:
    raw = _raw()
    raw["failures"]["skills"]["robot2_carry_box"]["weights"] = {"TIMEOUT": 0.0}
    with pytest.raises(ConfigError, match="权重和"):
        build_config(raw)


def test_skill_rate_must_be_a_probability() -> None:
    raw = _raw()
    raw["failures"]["skills"]["robot2_carry_box"]["rate"] = 1.2
    with pytest.raises(ConfigError, match="rate"):
        build_config(raw)


def test_valid_config_still_loads() -> None:
    config = build_config(_raw())
    assert config.target_screws == 6
    assert config.budget.soft_limit == 40


# ------------------------------------------------------------------ 策略注册表


def test_unknown_policy_name_is_rejected() -> None:
    for name in ("rule_noysy", "naiv", "rule", "nope"):
        with pytest.raises(ValueError, match="未知策略"):
            make_policy(name, CONFIG)
        with pytest.raises(ValueError, match="未知策略"):
            verifier_kind_for(name)


def test_known_policies_are_registered() -> None:
    assert set(POLICIES) == {"naive", "retry_only", "rule_oracle", "rule_noisy"}
    for name in POLICIES:
        assert make_policy(name, CONFIG) is not None
        assert verifier_kind_for(name) in ("oracle", "noisy")


def test_cli_rejects_unknown_policy_name(tmp_path) -> None:
    code = main(
        [
            "sweep",
            "--seeds", "1",
            "--failure-rates", "0",
            "--policies", "rule_noysy",
            "--out", str(tmp_path / "out"),
        ]
    )
    assert code != 0


def test_cli_rejects_unknown_policy_in_verifier_sweep(tmp_path) -> None:
    code = main(
        [
            "sweep-verifier",
            "--seeds", "1",
            "--levels", "0",
            "--policies", "nope",
            "--out", str(tmp_path / "out"),
        ]
    )
    assert code != 0
