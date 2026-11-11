"""JSONL 读写。全程显式 UTF-8，中文不转义。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator


def dumps(record: Any) -> str:
    """序列化成一行 JSON（键有序、中文保留、行内无换行）。"""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_jsonl(path: str | Path, records: Iterable[Any]) -> int:
    """把记录写入 JSONL 文件（覆盖），返回写入条数。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(dumps(record))
            fh.write("\n")
            count += 1
    return count


def append_jsonl(path: str | Path, record: Any) -> None:
    """追加一条记录。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(record))
        fh.write("\n")


def read_jsonl(path: str | Path) -> list[Any]:
    """读取整个 JSONL 文件。空行会被跳过。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"JSONL 文件不存在：{p}")
    out: list[Any] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def iter_jsonl(path: str | Path) -> Iterator[Any]:
    """逐条读取 JSONL（流式）。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"JSONL 文件不存在：{p}")
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
