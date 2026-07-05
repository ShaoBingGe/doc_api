"""架构守护：ocr_optimizer 不得反向依赖 app.services（结构第二轮 A0）.

目标依赖方向（repository-structure.md §六）：
    api/v1 → app/services → app/ocr_optimizer/service → processors
                  │                    │
                  └──── app/models ◄───┘（+ 择机新增 app/domain 中立层）

`app/services` 可以 import `ocr_optimizer`（编排引擎）；`ocr_optimizer` 是被
编排的引擎层，**不得**反向 import `app.services`——否则形成双向依赖，靠
函数内延迟 import 苟活，迟早循环。

本测试用 AST 扫描 ocr_optimizer 下每个 .py 的**全部** import（含函数内
延迟 import，因为遍历所有 Import/ImportFrom 节点），断言任何对
`app.services.*` 的引用都在 `ALLOWED_REVERSE` 白名单内。

白名单 = 结构还债的「待办清单」：每消一处反向依赖就删一行，删空即毕业。
新增反向依赖（不在白名单）→ 测试红，逼开发者要么走 domain 层、要么显式
把债务登记进白名单（评审可见）。
"""
from __future__ import annotations

import ast
from pathlib import Path

# 现存反向依赖（已知技术债，靠函数内延迟 import 苟着）。
# 键：repo 相对路径；值：该文件允许 import 的 app.services.* 模块全名集合。
# 还债时逐条删除——A1 消 pending_edits_service（4 文件），A2 消
# document_service（doc_sync + customer_iteration 的 reprocess）。
ALLOWED_REVERSE: dict[str, set[str]] = {
    # A1 消 pending_edits_service（overlay 抽 domain）；A2 消
    # _rewrite_structured_data_keys（后处理纯函数下沉 extraction_pipeline）。
    # 剩下的 document_service 依赖只有 reprocess_document —— 它是编排 OCR
    # 的原语，引擎侧 re-OCR（doc_sync Phase 25 / customer_iteration fork sweep）
    # 合法地请求文档层「重新提取」，不属于纯数据/知识借用。这是**永久例外**
    # （下沉它等于把整条 OCR 提取管线搬出 document_service，方向反而更乱）。
    "app/ocr_optimizer/service/doc_sync.py": {
        "app.services.document_service",  # reprocess_document（合法 engine→document 原语）
    },
    "app/ocr_optimizer/service/customer_iteration.py": {
        "app.services.document_service",  # reprocess_document（同上）
    },
}

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOT = _BACKEND_ROOT / "app" / "ocr_optimizer"


def _services_imports(tree: ast.AST) -> set[str]:
    """Return the set of `app.services.X` module names imported anywhere in a
    parsed module (module-level OR inside functions — we walk every node)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "app.services":
                # `from app.services import pending_edits_service [as x]`
                for alias in node.names:
                    found.add(f"app.services.{alias.name}")
            elif mod == "app.services" or mod.startswith("app.services."):
                # `from app.services.document_service import foo`
                found.add(mod)
        elif isinstance(node, ast.Import):
            # `import app.services.document_service [as x]`
            for alias in node.names:
                if alias.name.startswith("app.services"):
                    found.add(alias.name)
    return found


def test_ocr_optimizer_does_not_reverse_depend_on_services():
    violations: list[str] = []
    for py in sorted(_SCAN_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(_BACKEND_ROOT).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        imports = _services_imports(tree)
        allowed = ALLOWED_REVERSE.get(rel, set())
        for mod in sorted(imports - allowed):
            violations.append(
                f"{rel} imports {mod} (not in ALLOWED_REVERSE). "
                f"改走 app/domain 中立层，或把债务显式登记进白名单。"
            )
    assert not violations, "反向依赖越界：\n" + "\n".join(violations)


def test_allowlist_has_no_stale_entries():
    """白名单不得有「已还清但没删」的僵尸条目——保证它精确反映现状，
    删空即真的毕业（否则白名单会掩盖回归）。"""
    stale: list[str] = []
    for rel, allowed in ALLOWED_REVERSE.items():
        py = _BACKEND_ROOT / rel
        assert py.exists(), f"ALLOWED_REVERSE 指向不存在的文件: {rel}"
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        actual = _services_imports(tree)
        for mod in sorted(allowed - actual):
            stale.append(f"{rel}: {mod} 已不再 import，请从 ALLOWED_REVERSE 删除")
    assert not stale, "白名单有僵尸条目：\n" + "\n".join(stale)
