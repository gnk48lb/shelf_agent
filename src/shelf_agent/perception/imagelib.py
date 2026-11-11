"""图像库：把箱内/箱外螺丝的数量画成图片。

``SyntheticImageLibrary`` 用 Pillow 画出箱子与螺丝，**图像确实编码计数**，
因此可以拿它做可视化调试，也可以将来直接喂给 VLM。

``ImageRef.meta`` 里带着真值，只给 Oracle 验证器用；暴露给 agent 的只有
``image_id`` 与 ``path``（见 :meth:`ImageRef.public`）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from PIL import Image, ImageDraw

#: 画布尺寸（宽、高）。
CANVAS_W, CANVAS_H = 256, 320

#: 箱子在画布上的位置。
BOX_RECT = (40, 30, 216, 206)

_BACKGROUND = (236, 236, 236)
_BOX_FILL = (205, 205, 198)
_BOX_EDGE = (60, 60, 60)
_INSIDE_COLOR = (36, 78, 168)
_OUTSIDE_COLOR = (188, 52, 52)


@dataclass(frozen=True)
class ImageRef:
    """一张图片的引用。

    ``meta`` 是真值（仅供 Oracle 验证器与测试使用），**不得**进入 agent 输入。
    """

    image_id: str
    path: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        """对外可见的字段（只有这两个）。"""
        return {"image_id": self.image_id, "path": self.path}


class ImageLibrary(Protocol):
    """取图接口。"""

    def capture(self, truth: Mapping[str, Any]) -> ImageRef:
        """按真值取一张图。``truth`` 需含 ``inside`` 与 ``outside`` 计数。"""


def _inside_positions() -> list[tuple[int, int]]:
    """箱内螺丝的固定位置（4 列 x 3 行 = 12 个槽）。"""
    return [(60 + 40 * c, 60 + 45 * r) for r in range(3) for c in range(4)]


def _outside_positions() -> list[tuple[int, int]]:
    """箱外螺丝的固定位置（画布底部 4 列 x 2 行 = 8 个槽）。"""
    return [(60 + 45 * c, 240 + 42 * r) for r in range(2) for c in range(4)]


def draw_box(inside: int, outside: int) -> Image.Image:
    """画出箱内 ``inside`` 颗、箱外 ``outside`` 颗螺丝的图像。"""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), _BACKGROUND)
    draw = ImageDraw.Draw(img)
    draw.rectangle(BOX_RECT, fill=_BOX_FILL, outline=_BOX_EDGE, width=3)
    for pos in _inside_positions()[:inside]:
        x, y = pos
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=_INSIDE_COLOR, outline=_BOX_EDGE)
    for pos in _outside_positions()[:outside]:
        x, y = pos
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=_OUTSIDE_COLOR, outline=_BOX_EDGE)
    return img


class SyntheticImageLibrary:
    """合成图库。

    默认不落盘（``save_images=False``），此时 ``path`` 为空串；开启后写入
    ``out_dir`` 下的 PNG。
    """

    def __init__(
        self,
        save_images: bool = False,
        out_dir: str | Path = "out/images",
        prefix: str = "img",
    ) -> None:
        self.save_images = save_images
        self.out_dir = Path(out_dir)
        self.prefix = prefix
        self._counter = 0

    def capture(self, truth: Mapping[str, Any]) -> ImageRef:
        inside = int(truth.get("inside", 0))
        outside = int(truth.get("outside", 0))
        self._counter += 1
        digest = hashlib.sha256(f"{inside}:{outside}:{self._counter}".encode("utf-8")).hexdigest()
        image_id = f"{self.prefix}_{self._counter:05d}_{digest[:8]}"
        path = ""
        if self.save_images:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            target = self.out_dir / f"{image_id}.png"
            draw_box(inside, outside).save(target, format="PNG")
            path = str(target)
        return ImageRef(image_id=image_id, path=path, meta={"inside": inside, "outside": outside})


class FolderImageLibrary:
    """真机照片图库：读 ``labels.json``，按真值 (inside, outside) 匹配图片。

    ``labels.json`` 的格式::

        {"01.jpg": {"inside_count": 0, "outside_count": 0, "is_clear": true, "note": "..."}}

    匹配不上时回退到合成图（``fallback``），保证仿真不会因为图库不全而中断。
    """

    def __init__(
        self,
        folder: str | Path,
        labels_name: str = "labels.json",
        fallback: ImageLibrary | None = None,
    ) -> None:
        self.folder = Path(folder)
        self.fallback = fallback if fallback is not None else SyntheticImageLibrary()
        labels_path = self.folder / labels_name
        self.labels: dict[str, dict[str, Any]] = {}
        if labels_path.is_file():
            with labels_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError(f"labels.json 顶层必须是对象：{labels_path}")
            self.labels = {str(k): dict(v) for k, v in raw.items()}
        self._index: dict[tuple[int, int], list[str]] = {}
        for name, meta in self.labels.items():
            key = (int(meta.get("inside_count", 0)), int(meta.get("outside_count", 0)))
            self._index.setdefault(key, []).append(name)
        for names in self._index.values():
            names.sort()
        self._counter = 0

    def capture(self, truth: Mapping[str, Any]) -> ImageRef:
        inside = int(truth.get("inside", 0))
        outside = int(truth.get("outside", 0))
        names = self._index.get((inside, outside))
        if not names:
            return self.fallback.capture(truth)
        self._counter += 1
        name = names[self._counter % len(names)]
        meta = dict(self.labels.get(name, {}))
        return ImageRef(
            image_id=name,
            path=str(self.folder / name),
            meta={
                "inside": inside,
                "outside": outside,
                "clear": bool(meta.get("is_clear", True)),
                "note": meta.get("note", ""),
            },
        )
