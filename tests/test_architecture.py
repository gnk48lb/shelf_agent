"""架构约束的自动化检查：用 AST 扫描 src/，强制 TASK.md §4.3 的分层与依赖规则。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "shelf_agent"

#: 包 -> 层号（数字越大越靠上，只能向下依赖）。
LAYERS: dict[str, int] = {
    "shelf_agent.utils": 0,
    "shelf_agent.contracts": 1,
    "shelf_agent.perception": 1,
    "shelf_agent.env": 2,
    "shelf_agent.runtime": 3,
    "shelf_agent.agents": 4,
    "shelf_agent.eval": 5,
}

#: 顶层模块 config.py 属于 L0。
ROOT_LAYER = 0

#: env / contracts / perception / runtime 禁止出现的网络与 LLM 库。
FORBIDDEN_NETWORK_MODULES = {
    "requests", "httpx", "aiohttp", "urllib3", "socket", "http",
    "openai", "anthropic", "langchain", "langgraph", "litellm", "ollama",
    "transformers", "torch", "pydantic", "gym", "gymnasium",
}

#: 这些包禁止导入网络库（urllib 由 L4 的 llm_client 独占使用）。
NO_NETWORK_PACKAGES = ("env", "contracts", "perception", "runtime")

#: 允许出现 print 的模块（CLI 与 report 是输出层）。
PRINT_ALLOWED = {"eval/cli.py", "eval/report.py"}

#: 允许出现 time.sleep 的模块（LLM 客户端的退避 sleep 必须可注入）。
SLEEP_ALLOWED = {"agents/llm_client.py"}


def _module_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py"))


def _rel_module(path: Path) -> str:
    """src/shelf_agent/env/backend.py -> shelf_agent.env.backend"""
    parts = list(path.relative_to(SRC.parent).with_suffix("").parts)
    if path.name == "__init__.py":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(path: Path) -> str:
    """文件所属的包名（__init__.py 归到父包，config.py 归到 shelf_agent）。"""
    module = _rel_module(path)
    if path.name == "__init__.py":
        return module
    return module.rpartition(".")[0]


def _layer_of_package(package: str) -> int | None:
    if package == "shelf_agent":
        return ROOT_LAYER
    return LAYERS.get(package)


def _resolve_relative(current_package: str, level: int, module: str | None) -> str:
    """把相对 import 解析成绝对模块名。"""
    parts = current_package.split(".")
    if level > 1:
        parts = parts[: -(level - 1)]
    if module:
        parts = parts + module.split(".")
    return ".".join(p for p in parts if p)


def _imported_modules(tree: ast.AST, current_package: str) -> list[tuple[str, ast.AST]]:
    """收集文件里 import 的所有绝对模块名。"""
    found: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(current_package, node.level, node.module)
            elif node.module:
                base = node.module
            else:
                continue
            found.append((base, node))
    return found


def _module_package(module: str) -> str:
    """取模块名所属的包（顶层模块返回自身）。"""
    return module.rpartition(".")[0] if "." in module else module


def _target_package(module: str, node: ast.AST) -> str:
    """``from a.b import c`` 时 c 可能是子模块，取更深的那一层。"""
    package = _module_package(module)
    if isinstance(node, ast.ImportFrom) and node.module and "." in module:
        for alias in node.names:
            candidate = f"{module}.{alias.name}"
            if (SRC / Path(*candidate.split(".")[1:])).is_dir() or (
                SRC / Path(*candidate.split(".")[1:]) / "__init__.py"
            ).is_file():
                return candidate
    return package


@pytest.fixture(scope="module")
def parsed() -> list[tuple[Path, ast.Module]]:
    out = []
    for path in _module_files():
        out.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return out


def test_src_layout_exists() -> None:
    assert (SRC / "__init__.py").is_file()
    assert (SRC / "config.py").is_file()


def test_no_upward_imports(parsed: list[tuple[Path, ast.Module]]) -> None:
    """任何模块都不得 import 比自己更高层的模块。"""
    violations: list[str] = []
    for path, tree in parsed:
        package = _package_of(path)
        current = _layer_of_package(package)
        assert current is not None, f"未登记的包：{package}"
        for module, node in _imported_modules(tree, package):
            if not module.startswith("shelf_agent"):
                continue
            target_pkg = _target_package(module, node)
            target = _layer_of_package(target_pkg)
            if target is None:
                continue
            if target > current:
                violations.append(
                    f"{path.relative_to(SRC)} 向上 import 了 {target_pkg}（层 {current} -> {target}）"
                )
    assert not violations, "分层违规：\n" + "\n".join(violations)


def test_contracts_and_perception_do_not_import_each_other(parsed: list[tuple[Path, ast.Module]]) -> None:
    """§4.3：contracts 与 perception 同层互不 import。"""
    violations: list[str] = []
    for path, tree in parsed:
        package = _package_of(path)
        for module, node in _imported_modules(tree, package):
            if not module.startswith("shelf_agent"):
                continue
            target_pkg = _target_package(module, node)
            if package == "shelf_agent.contracts" and target_pkg.startswith("shelf_agent.perception"):
                violations.append(f"{path.relative_to(SRC)} import 了 {target_pkg}")
            if package == "shelf_agent.perception" and target_pkg.startswith("shelf_agent.contracts"):
                violations.append(f"{path.relative_to(SRC)} import 了 {target_pkg}")
    assert not violations, "同层互引违规：\n" + "\n".join(violations)


def test_agents_do_not_import_env(parsed: list[tuple[Path, ast.Module]]) -> None:
    """§4.3：agents 只经 ToolExecutor 与契约类型与环境交互，不得 import env.*。"""
    violations: list[str] = []
    for path, tree in parsed:
        if _package_of(path) != "shelf_agent.agents":
            continue
        for module, _node in _imported_modules(tree, _package_of(path)):
            if module.startswith("shelf_agent.env"):
                violations.append(f"{path.relative_to(SRC)} import 了 {module}")
    assert not violations, "agents 不得直接依赖 env：\n" + "\n".join(violations)


def test_core_packages_have_no_network_imports(parsed: list[tuple[Path, ast.Module]]) -> None:
    """§2/§4.3：env、contracts、perception、runtime 不得引入任何 LLM / 网络库。"""
    violations: list[str] = []
    for path, tree in parsed:
        package = _package_of(path)
        short = package.rpartition(".")[2]
        if short not in NO_NETWORK_PACKAGES:
            continue
        for module, _node in _imported_modules(tree, package):
            root = module.split(".")[0]
            if root in FORBIDDEN_NETWORK_MODULES:
                violations.append(f"{path.relative_to(SRC)} import 了 {module}")
    assert not violations, "核心包禁止联网/LLM 依赖：\n" + "\n".join(violations)


def test_no_time_sleep_outside_llm_client(parsed: list[tuple[Path, ast.Module]]) -> None:
    """§2：库代码中禁止 time.sleep，只有 llm_client.py 的可注入退避可以。"""
    violations: list[str] = []
    for path, tree in parsed:
        rel = path.relative_to(SRC).as_posix()
        if rel in SLEEP_ALLOWED:
            continue
        sleeps_time = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "time":
                if any(a.name == "sleep" for a in node.names):
                    sleeps_time = True
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "sleep":
                    if isinstance(func.value, ast.Name) and func.value.id == "time":
                        violations.append(f"{rel} 调用了 time.sleep()")
                    elif sleeps_time:
                        violations.append(f"{rel} 调用了 sleep()")
    assert not violations, "库代码中出现 sleep：\n" + "\n".join(violations)


def test_no_print_in_library_code(parsed: list[tuple[Path, ast.Module]]) -> None:
    """§7：库代码里不要 print（CLI 与 report 除外）。"""
    violations: list[str] = []
    for path, tree in parsed:
        rel = path.relative_to(SRC).as_posix()
        if rel in PRINT_ALLOWED:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                violations.append(f"{rel} 使用了 print()")
    assert not violations, "库代码中出现 print：\n" + "\n".join(violations)


def test_no_forbidden_third_party_dependencies() -> None:
    """§2：第三方依赖只允许 pyyaml / pillow / matplotlib（dev: pytest 系）。"""
    pyproject = SRC.parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    allowed = {"pyyaml", "pillow", "matplotlib", "pytest", "pytest-cov"}
    found = set(re.findall(r"^\s*\"([a-zA-Z0-9_-]+)[><=~ ]", text, flags=re.MULTILINE))
    found |= set(re.findall(r"^\s*\"([a-zA-Z0-9_-]+)\"", text, flags=re.MULTILINE))
    disallowed = {name for name in found if name not in allowed and name not in {
        "shelf-agent", "hatchling", "build", "dist",
    }}
    assert not disallowed, f"出现未批准的依赖：{sorted(disallowed)}"
