"""验证器与图像库单测。"""

from __future__ import annotations

import importlib.util
import json
import random

import pytest

from shelf_agent.perception.completion import Report, is_complete
from shelf_agent.perception.imagelib import SyntheticImageLibrary, draw_box
from shelf_agent.perception.verifier import (
    NoisyVerifier,
    OracleVerifier,
    ScriptedVerifier,
    load_confusion_table,
)

TARGET = 6
INSIDE_COLOR = (36, 78, 168)


def _ref(inside: int, outside: int):
    library = SyntheticImageLibrary()
    return library.capture({"inside": inside, "outside": outside})


def test_pillow_is_available() -> None:
    """Pillow 缺失时必须明确失败，不允许静默跳过。"""
    assert importlib.util.find_spec("PIL") is not None, "缺少 Pillow，图像相关测试无法运行"


def test_oracle_verifier_is_exact() -> None:
    verifier = OracleVerifier()
    report = verifier.verify(_ref(4, 2), random.Random(0))
    assert report == Report(inside=4, outside=2, clear=True)


def test_image_ref_public_hides_truth() -> None:
    ref = _ref(3, 1)
    assert ref.meta == {"inside": 3, "outside": 1}
    assert set(ref.public()) == {"image_id", "path"}
    assert "inside" not in ref.public() and "outside" not in ref.public()


def test_noisy_verifier_is_reproducible_for_same_seed() -> None:
    verifier = NoisyVerifier(target=TARGET)

    def run() -> list[tuple[int, int, bool]]:
        rng = random.Random(1234)
        return [
            (
                verifier.verify(_ref(5, 1), rng).inside,
                verifier.verify(_ref(5, 1), rng).outside,
                verifier.verify(_ref(5, 1), rng).clear,
            )
            for _ in range(20)
        ]

    assert run() == run()


def test_noisy_verifier_boundary_confusion_creates_false_pass() -> None:
    """真值 N-1 时，boundary_confusion 会把它报成 N —— 错误放行的来源。"""
    verifier = NoisyVerifier(target=TARGET, p_inside_correct=1.0, boundary_confusion=1.0)
    report = verifier.verify(_ref(TARGET - 1, 0), random.Random(0))
    assert report.inside == TARGET
    assert is_complete(report, TARGET) is True


def test_noisy_verifier_without_boundary_confusion_keeps_short_count() -> None:
    verifier = NoisyVerifier(target=TARGET, p_inside_correct=1.0, boundary_confusion=0.0)
    report = verifier.verify(_ref(TARGET - 1, 0), random.Random(0))
    assert report.inside == TARGET - 1
    assert is_complete(report, TARGET) is False


def test_noisy_verifier_unclear_rate() -> None:
    verifier = NoisyVerifier(target=TARGET, p_unclear=1.0)
    report = verifier.verify(_ref(TARGET, 0), random.Random(0))
    assert report.clear is False
    verifier2 = NoisyVerifier(target=TARGET, p_unclear=0.0)
    assert verifier2.verify(_ref(TARGET, 0), random.Random(0)).clear is True


def test_noisy_verifier_unclear_rate_is_approximately_right() -> None:
    verifier = NoisyVerifier(target=TARGET, p_inside_correct=1.0, p_unclear=0.10)
    rng = random.Random(7)
    unclear = sum(1 for _ in range(2000) if not verifier.verify(_ref(TARGET, 0), rng).clear)
    assert 0.08 < unclear / 2000 < 0.12


def test_noisy_verifier_outside_miss() -> None:
    verifier = NoisyVerifier(target=TARGET, p_outside_miss=1.0)
    report = verifier.verify(_ref(TARGET, 2), random.Random(0))
    assert report.outside == 0
    verifier2 = NoisyVerifier(target=TARGET, p_outside_miss=0.0)
    assert verifier2.verify(_ref(TARGET, 2), random.Random(0)).outside == 2


def test_noisy_verifier_inside_error_is_plus_or_minus_one() -> None:
    verifier = NoisyVerifier(target=TARGET, p_inside_correct=0.0, boundary_confusion=0.0)
    rng = random.Random(11)
    seen = set()
    for _ in range(200):
        value = verifier.verify(_ref(4, 0), rng).inside
        assert abs(value - 4) == 1
        seen.add(value)
    assert seen == {3, 5}


def test_noisy_verifier_never_reports_negative_inside() -> None:
    verifier = NoisyVerifier(target=TARGET, p_inside_correct=0.0)
    rng = random.Random(5)
    for _ in range(100):
        assert verifier.verify(_ref(0, 0), rng).inside >= 0


def test_confusion_table_loading(tmp_path) -> None:
    path = tmp_path / "table.json"
    path.write_text(
        json.dumps({"6": {"6": 0.9, "5": 0.1}, "5": {"6": 0.2, "5": 0.8}}), encoding="utf-8"
    )
    table = load_confusion_table(path)
    assert table == {6: {6: 0.9, 5: 0.1}, 5: {6: 0.2, 5: 0.8}}
    verifier = NoisyVerifier.from_confusion_table(table, target=TARGET)
    rng = random.Random(3)
    values = {verifier.verify(_ref(6, 0), rng).inside for _ in range(50)}
    assert values <= {5, 6}


def test_confusion_table_missing_row_falls_back_to_parametric() -> None:
    verifier = NoisyVerifier.from_confusion_table({1: {1: 1.0}}, target=TARGET, p_inside_correct=1.0)
    assert verifier.verify(_ref(6, 0), random.Random(0)).inside == 6


def test_scripted_verifier_returns_in_order() -> None:
    verifier = ScriptedVerifier([Report(3, 0, True), Report(6, 0, True)])
    assert verifier.verify(_ref(0, 0), random.Random(0)).inside == 3
    assert verifier.verify(_ref(0, 0), random.Random(0)).inside == 6
    assert verifier.calls == 2


def test_scripted_verifier_repeats_last_after_exhaustion() -> None:
    verifier = ScriptedVerifier([Report(6, 0, True)])
    verifier.verify(_ref(0, 0), random.Random(0))
    assert verifier.verify(_ref(0, 0), random.Random(0)).inside == 6


def test_scripted_verifier_requires_reports() -> None:
    with pytest.raises(ValueError):
        ScriptedVerifier([])


# --------------------------------------------------------------------- 图像


def _count_color(image, color) -> int:
    for count, pixel in image.getcolors(maxcolors=1 << 20) or []:
        if pixel == color:
            return count
    return 0


def test_synthetic_image_content_changes_with_counts() -> None:
    assert draw_box(2, 0).tobytes() != draw_box(3, 0).tobytes()
    assert draw_box(2, 0).tobytes() != draw_box(2, 1).tobytes()


def test_synthetic_image_encodes_inside_count() -> None:
    two = _count_color(draw_box(2, 0), INSIDE_COLOR)
    four = _count_color(draw_box(4, 0), INSIDE_COLOR)
    assert four > two > 0


def test_synthetic_image_encodes_outside_count() -> None:
    zero = _count_color(draw_box(0, 0), (188, 52, 52))
    two = _count_color(draw_box(0, 2), (188, 52, 52))
    assert two > zero == 0


def test_synthetic_library_not_saving_by_default() -> None:
    library = SyntheticImageLibrary()
    ref = library.capture({"inside": 2, "outside": 0})
    assert ref.path == ""
    assert ref.image_id


def test_synthetic_library_saves_when_asked(tmp_path) -> None:
    library = SyntheticImageLibrary(save_images=True, out_dir=tmp_path / "images")
    ref = library.capture({"inside": 2, "outside": 0})
    assert ref.path.endswith(".png")
    assert (tmp_path / "images" / f"{ref.image_id}.png").is_file()
