"""[可] 扩展配置（2 箱 2 槽）、真机图库、gym 风格环境。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shelf_agent.config import load_config
from shelf_agent.contracts.spec import build_spec_set
from shelf_agent.contracts.types import ToolCall
from shelf_agent.eval.scenarios import make_episode
from shelf_agent.perception.imagelib import FolderImageLibrary, SyntheticImageLibrary
from shelf_agent.runtime.tool_env import ToolEnv
from shelf_agent.utils.enums import BoxLocation

CONFIG = load_config("configs/extended.yaml")
BOXES = ("box_1", "box_2")
SLOTS = ("slot_1", "slot_2")


def _episode():
    return make_episode(
        CONFIG,
        seed=0,
        failure_rate=0.0,
        verifier_kind="oracle",
        box_ids=BOXES,
        slot_ids=SLOTS,
    )


def test_extended_config_has_box_parameters() -> None:
    spec = build_spec_set(CONFIG)
    install = spec.get("robot1_install_screws")
    assert [p.name for p in install.params] == ["box_id"]
    carry = spec.get("robot2_carry_box")
    assert [p.name for p in carry.params] == ["box_id", "slot_id"]
    assert carry.params[1].required is False


def test_extended_config_validates_required_args() -> None:
    from shelf_agent.contracts.schema import ArgsError, validate_args

    spec = build_spec_set(CONFIG)
    with pytest.raises(ArgsError, match="缺少必填参数 'box_id'"):
        validate_args(spec.get("verify_box"), {})


def test_both_boxes_can_be_delivered() -> None:
    """两箱均能装满、验证并上架。"""
    ep = _episode()
    for box_id in BOXES:
        install = ep.executor.execute(ToolCall("robot1_install_screws", {"box_id": box_id}))
        assert install.status == "done"
        report = ep.executor.execute(ToolCall("verify_box", {"box_id": box_id}))
        assert report.data == {"inside": 6, "outside": 0, "clear": True}
        carry = ep.executor.execute(ToolCall("robot2_carry_box", {"box_id": box_id}))
        assert carry.status == "done"
    for box_id in BOXES:
        assert ep.world.box(box_id).location is BoxLocation.SHELF
    assert set(ep.world.shelf_slots.values()) == set(BOXES)


def test_occupied_slot_makes_the_box_switch_slots() -> None:
    """期望的槽位被占时，换另一个空槽。"""
    ep = _episode()
    for box_id in BOXES:
        ep.executor.execute(ToolCall("robot1_install_screws", {"box_id": box_id}))
    first = ep.executor.execute(
        ToolCall("robot2_carry_box", {"box_id": "box_1", "slot_id": "slot_1"})
    )
    assert first.status == "done"
    assert ep.world.shelf_slots["slot_1"] == "box_1"
    second = ep.executor.execute(
        ToolCall("robot2_carry_box", {"box_id": "box_2", "slot_id": "slot_1"})
    )
    assert second.status == "done"
    assert ep.world.shelf_slots["slot_1"] == "box_1", "已被占用的槽位不能被抢"
    assert ep.world.shelf_slots["slot_2"] == "box_2", "应当换到另一个空槽"


def test_no_free_slot_fails_the_carry() -> None:
    ep = _episode()
    for box_id in BOXES:
        ep.executor.execute(ToolCall("robot1_install_screws", {"box_id": box_id}))
    ep.executor.execute(ToolCall("robot2_carry_box", {"box_id": "box_1", "slot_id": "slot_1"}))
    ep.executor.execute(ToolCall("robot2_carry_box", {"box_id": "box_2", "slot_id": "slot_2"}))
    # 把槽位占满后再搬：没有空槽可用
    ep.world.shelf_slots["slot_1"] = "box_1"
    ep.world.shelf_slots["slot_2"] = "box_2"
    ep.world.box("box_1").location = BoxLocation.STATION
    result = ep.executor.execute(ToolCall("robot2_carry_box", {"box_id": "box_1"}))
    assert result.status == "timeout"
    assert ep.world.box("box_1").location is BoxLocation.STATION


# --------------------------------------------------------------- 真机图库


def test_folder_image_library_matches_by_counts(tmp_path: Path) -> None:
    (tmp_path / "01.jpg").write_bytes(b"fake")
    (tmp_path / "02.jpg").write_bytes(b"fake")
    (tmp_path / "labels.json").write_text(
        json.dumps(
            {
                "01.jpg": {"inside_count": 6, "outside_count": 0, "is_clear": True, "note": "装满"},
                "02.jpg": {"inside_count": 3, "outside_count": 2, "is_clear": False, "note": ""},
            }
        ),
        encoding="utf-8",
    )
    library = FolderImageLibrary(tmp_path)
    ref = library.capture({"inside": 6, "outside": 0})
    assert ref.image_id == "01.jpg"
    assert ref.path.endswith("01.jpg")
    assert ref.meta["inside"] == 6
    assert ref.meta["outside"] == 0
    assert ref.meta["clear"] is True
    other = library.capture({"inside": 3, "outside": 2})
    assert other.image_id == "02.jpg"
    assert other.meta["clear"] is False


def test_folder_image_library_falls_back_to_synthetic(tmp_path: Path) -> None:
    (tmp_path / "labels.json").write_text(
        json.dumps({"01.jpg": {"inside_count": 6, "outside_count": 0}}), encoding="utf-8"
    )
    library = FolderImageLibrary(tmp_path, fallback=SyntheticImageLibrary())
    ref = library.capture({"inside": 2, "outside": 1})
    assert ref.image_id.startswith("img_")
    assert ref.path == ""


# ------------------------------------------------------------ gym 风格环境


def test_tool_env_reset_and_step() -> None:
    env = ToolEnv(CONFIG)
    obs, info = env.reset(seed=0)
    assert obs["steps"] == 0
    assert info["seed"] == 0
    obs, reward, terminated, truncated, info = env.step(
        ToolCall("robot1_install_screws", {"box_id": "box_1"})
    )
    assert obs["status"] == "done"
    assert obs["steps"] == 1
    assert terminated is False
    assert truncated is False
    assert reward < 1.0  # 还没上架


def test_tool_env_terminates_on_finish() -> None:
    env = ToolEnv(CONFIG)
    env.reset(seed=0)
    obs, reward, terminated, truncated, info = env.step(
        ToolCall("finish", {"success": True, "summary": "done"})
    )
    assert terminated is True
    assert obs["status"] == "ok"


def test_tool_env_observation_has_no_truth() -> None:
    env = ToolEnv(CONFIG)
    env.reset(seed=0)
    obs, *_ = env.step(ToolCall("robot1_install_screws", {"box_id": "box_1"}))
    assert set(obs) == {
        "t_s",
        "steps",
        "status",
        "error_code",
        "message",
        "duration_s",
        "data",
    }
    assert "location" not in str(obs)
