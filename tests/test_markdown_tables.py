"""markdown 表格的列数必须一致：分隔符与表头列数不符时 GFM 解析失败，整张表退化成纯文本。

背景：README §7.2 的验证器扫描表曾用 9 列分隔符配 8 列表头（从 §7.1 复制时留下的），
在 VS Code 预览里整张表显示成一坨带竖线的原文。生成器
``eval/summary.py`` 与 ``scripts/render_readme_tables.py`` 都有同一个笔误，
这里把「表头 / 分隔符 / 数据行列数一致」钉死，防止再次回归。
"""

from __future__ import annotations

import re
from pathlib import Path

from shelf_agent.config import load_config
from shelf_agent.eval.sweep import run_verifier_sweep

ROOT = Path(__file__).resolve().parents[1]
SEP_RE = re.compile(r"^\|[-:| ]+\|$")
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def _cells(line: str) -> list[str]:
    """按**未转义**的竖线切单元格；``\\|`` 是单元格内容里的竖线，不分格。"""
    return re.split(r"(?<!\\)\|", line.strip().strip("|"))


def _table_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """找出所有 markdown 表格，返回 ``(表头行号, 表格各行)``。"""
    blocks: list[tuple[int, list[str]]] = []
    i = 0
    while i + 1 < len(lines):
        if lines[i].startswith("|") and SEP_RE.match(lines[i + 1].strip()):
            block = [lines[i], lines[i + 1]]
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                block.append(lines[j])
                j += 1
            blocks.append((i + 1, block))
            i = j
        else:
            i += 1
    return blocks


def _md_files() -> list[Path]:
    return sorted(
        p
        for p in ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in p.parts)
    )


def test_repo_markdown_tables_have_matching_column_counts() -> None:
    """仓库里每个 markdown 表格：表头列数 == 分隔符列数 == 各数据行列数。"""
    problems: list[str] = []
    for path in _md_files():
        lines = path.read_text(encoding="utf-8").split("\n")
        for lineno, block in _table_blocks(lines):
            header, sep, *rows = block
            width = len(_cells(header))
            if len(_cells(sep)) != width:
                problems.append(
                    f"{path.relative_to(ROOT)}:{lineno} 分隔符 {len(_cells(sep))} 列 != 表头 {width} 列"
                )
            for offset, row in enumerate(rows):
                if len(_cells(row)) != width:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{lineno + 2 + offset} "
                        f"数据行 {len(_cells(row))} 列 != 表头 {width} 列"
                    )
    assert not problems, "markdown 表格列数不一致：\n" + "\n".join(problems)


def test_generated_verifier_markdown_has_consistent_columns(tmp_path: Path) -> None:
    """生成器直接产出的 verifier_sweep.md 也要列数一致（钉住笔误源头）。"""
    run_verifier_sweep(
        load_config("configs/real_screw.yaml"),
        failure_rate=0.3,
        levels=(0.0, 1.0),
        seeds=5,
        policies=("rule_oracle",),
        out_dir=tmp_path,
    )
    lines = (tmp_path / "verifier_sweep.md").read_text(encoding="utf-8").split("\n")
    blocks = _table_blocks(lines)
    assert blocks, "verifier_sweep.md 里没有表格"
    for lineno, block in blocks:
        header, sep, *rows = block
        width = len(_cells(header))
        assert len(_cells(sep)) == width, f"第 {lineno} 行的分隔符列数与表头不一致"
        for row in rows:
            assert len(_cells(row)) == width, f"第 {lineno} 行的数据行列数与表头不一致"
