"""字段对齐与消融归一 —— 多源分层覆盖。

三个数据源，优先级**固定**（高者覆盖低者）：

    LHDN 税局 API  >  本地 UBL  >  OCR
    （已验证）        （ERP 报送副本，未验证）  （视觉识别）

为什么 LHDN 高于 UBL：`/raw` 返回的就是当初报送的那份 UBL，内容本应一致，
但 LHDN 额外带**校验状态**（Valid / Invalid / Cancelled）与官方 uuid；
本地 UBL 只是 ERP「声称」报送的内容，可能与税局实际受理的不一致。

核心原则（客户明确要求）：**税局/UBL 有的一律优先覆盖 OCR；只有高优先级
数据源没有该字段时，才保留低优先级的值。** 不做"口径不一致就跳过"的退让——
币种等口径问题在 ubl_mapper 里用税局自带汇率归一解决，而不是丢弃数据。

两个例外（永不被覆盖），因为它们不是"票面事实"而是平台/物理属性：
  • `page`    —— PDF 物理页码，税局无此概念
  • `docType` —— 平台分类口径

另外 UBL 只存金额绝对值、靠类型码表达贷项，故融合后按最终 invoiceType
统一恢复负号（`_apply_credit_note_signs`）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 源优先级（数字越大越权威）
SOURCE_PRIORITY = {"ocr": 0, "ubl": 1, "lhdn": 2}

_NEVER_OVERRIDE = {"page", "docType"}

_SIGNED_TOTALS = ("totalAmount", "totalNetAmount", "totalTaxAmount")
_SIGNED_LINE = ("quantity", "netAmount", "grossAmount", "tax")

# 行级字段：UBL/LHDN 不承载，位置合并时从 OCR 行继承
_LINE_OCR_ONLY = (
    "description", "orderNumber", "purchaseOrderNumber",
    "salesOrderNumber", "deliveryOrderNumber",
)

_VALID_STATUSES = {"valid"}


@dataclass
class FusionReport:
    """一次消融的完整账本 —— 放进 ExtractResponse.metadata，不进票据字段。"""

    enabled: bool = True
    strategy: str | None = None
    sources_used: list[str] = field(default_factory=list)
    refs_found: int = 0
    fetched: int = 0
    fused: int = 0
    ocr_skipped: bool = False
    links: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        d: dict[str, Any] = {
            "enabled": self.enabled,
            "refs_found": self.refs_found,
            "fetched": self.fetched,
            "fused": self.fused,
        }
        if self.strategy:
            d["strategy"] = self.strategy
        if self.sources_used:
            d["sources_used"] = self.sources_used
        if self.ocr_skipped:
            d["ocr_skipped"] = True
        if self.links:
            d["links"] = self.links
        if self.documents:
            d["documents"] = self.documents
        if self.skipped:
            d["skipped"] = self.skipped
        if self.error:
            d["error"] = self.error
        return d


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


# ── 公开 API ────────────────────────────────────────────────────────────────
def fuse_sources(
    entities: list[dict],
    layers: list[tuple[str, list[tuple[Any, dict]]]],
    *,
    require_valid_status: bool = True,
) -> tuple[list[dict], FusionReport]:
    """分层融合。

    Args:
        entities: 基底票据列表（通常是 OCR 结果；跳过 OCR 时由调用方给骨架）。
        layers: [(source, [(ref, mapped)])]，**按优先级从低到高**排列。
        require_valid_status: 仅对 lhdn 层生效 —— 非 Valid 只上报不覆盖。

    Returns:
        (融合后的 entities, FusionReport)
    """
    report = FusionReport()
    out = [dict(e) for e in entities]
    # field → 最终来源
    prov: list[dict[str, str]] = [{} for _ in out]

    ordered = sorted(layers, key=lambda x: SOURCE_PRIORITY.get(x[0], 0))
    total_refs = sum(len(p) for _, p in ordered)
    report.refs_found = total_refs
    report.fetched = total_refs
    report.sources_used = [s for s, p in ordered if p]

    fused_idx: set[int] = set()

    for source, pairs in ordered:
        if not pairs:
            continue
        for idx, (ref, mapped) in _associate(out, pairs, report, source):
            meta = dict(mapped.get("_myinvois") or {})
            status = str(meta.get("status") or "").strip()

            # 校验状态门只管税局层（本地 UBL 没有官方状态）
            if source == "lhdn" and require_valid_status \
                    and status and status.lower() not in _VALID_STATUSES:
                entry = {
                    "source": source, "uuid": meta.get("uuid"),
                    "status": status, "entity_index": idx,
                    "action": "reported_only",
                    "reason": f"税局校验状态为 {status}，不覆盖（已作废/未通过的单据不应被当成权威值）",
                }
                report.documents.append(entry)
                report.skipped.append(entry)
                continue

            overridden = _merge_one(out[idx], mapped, source, prov[idx])
            fused_idx.add(idx)
            report.documents.append({
                "source": source,
                "uuid": meta.get("uuid"),
                "status": status or None,
                "entity_index": idx,
                "action": "fused",
                "fields_overridden": overridden,
                "converted": meta.get("converted"),
            })

    # 逐字段来源汇总（哪些最终来自 OCR、哪些被更权威的源覆盖）
    for i, e in enumerate(out):
        for k, v in e.items():
            if k.startswith("_") or _is_empty(v):
                continue
            prov[i].setdefault(k, "ocr")
    for i, entry in enumerate(prov):
        if i in fused_idx:
            report.documents.append({
                "entity_index": i, "action": "provenance",
                "by_source": _group(entry),
            })

    report.fused = len(fused_idx)
    return out, report


def fuse_entities(
    entities: list[dict],
    mapped_by_ref: list[tuple[Any, dict]],
    *,
    require_valid_status: bool = True,
) -> tuple[list[dict], FusionReport]:
    """向后兼容入口：单一税局层。等价于 fuse_sources(..., [("lhdn", pairs)])。"""
    fused, report = fuse_sources(
        entities, [("lhdn", mapped_by_ref)],
        require_valid_status=require_valid_status,
    )
    # 兼容旧字段名（老调用方/测试读的是 fields_from_myinvois / fields_kept_from_ocr）
    for d in report.documents:
        if d.get("action") == "fused":
            d["fields_from_myinvois"] = d.get("fields_overridden", [])
        if d.get("action") == "provenance":
            d["fields_kept_from_ocr"] = d.get("by_source", {}).get("ocr", [])
    merged: list[dict] = []
    prov_by_idx = {d["entity_index"]: d for d in report.documents
                   if d.get("action") == "provenance"}
    for d in report.documents:
        if d.get("action") == "fused":
            p = prov_by_idx.get(d["entity_index"])
            if p:
                d["fields_kept_from_ocr"] = p.get("fields_kept_from_ocr", [])
        if d.get("action") != "provenance":
            merged.append(d)
    report.documents = merged
    return fused, report


def _group(field_to_source: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in sorted(field_to_source.items()):
        out.setdefault(v, []).append(k)
    return out


# ── 内部实现 ────────────────────────────────────────────────────────────────
def _associate(
    entities: list[dict],
    pairs: list[tuple[Any, dict]],
    report: FusionReport,
    source: str,
) -> list[tuple[int, tuple[Any, dict]]]:
    """把某一层的单据配到票据上。页码优先，其次发票号，最后单票直配。"""
    out: list[tuple[int, tuple[Any, dict]]] = []
    used: set[int] = set()

    for ref, mapped in pairs:
        if hasattr(ref, "as_dict"):
            report.links.append({
                **ref.as_dict(), "source_layer": source,
                "status": (mapped.get("_myinvois") or {}).get("status"),
            })

        idx: int | None = None
        if len(entities) == 1 and len(pairs) == 1 and not _page_conflict(ref, entities[0]):
            # 单票单码直配：二维码就印在这份文档上，绑定是事实而非猜测。
            # 唯一的例外是「附件页夹了另一张带码的票、而 OCR 只认出了主票」——
            # 那种情况下页码是硬证据（码在第 3 页、实体只覆盖第 1 页），
            # 用 _page_conflict 精确挡掉。不拿发票号做模糊比对：同一供应商的
            # 号段天然相似，而 OCR 读错号恰恰是最需要税局纠正的场景。
            idx = 0
        else:
            page = getattr(ref, "page", None)
            if page is not None:
                for i, ent in enumerate(entities):
                    if i in used:
                        continue
                    pages = ent.get("page")
                    if isinstance(pages, list) and page in pages:
                        idx = i
                        break
                    if isinstance(pages, (int, float)) and int(pages) == page:
                        idx = i
                        break
            if idx is None:
                num = str(mapped.get("invoiceNumber") or "").strip().upper()
                if num:
                    avail = [(i, str(entities[i].get("invoiceNumber") or "").strip().upper())
                             for i in range(len(entities)) if i not in used]
                    # 先精确匹配；子串匹配**仅在候选唯一时**才采纳。
                    # 旧写法「取首个子串命中」会让 INV-1 抢走 INV-12 的税局数据，
                    # 在「税局恒覆盖」下等于把整张票改写成另一张票。
                    exact = [i for i, onum in avail if onum and onum == num]
                    if len(exact) == 1:
                        idx = exact[0]
                    else:
                        loose = [i for i, onum in avail
                                 if onum and (onum in num or num in onum)]
                        if len(loose) == 1:
                            idx = loose[0]

        if idx is None or idx in used:
            report.skipped.append({
                "source": source,
                "uuid": (mapped.get("_myinvois") or {}).get("uuid")
                        or getattr(ref, "uuid", None),
                "page": getattr(ref, "page", None),
                "action": "skipped",
                "reason": "无法与任一票据可靠对齐（页码/发票号均未匹配），不做猜测",
            })
            continue

        used.add(idx)
        out.append((idx, (ref, mapped)))

    return out


def _page_conflict(ref, entity: dict) -> bool:
    """二维码所在页与实体覆盖页是否**明确**冲突。

    仅在两侧页码都存在且不相交时判冲突；任一侧缺页码 → 返回 False（不阻断）。
    失配时回退纯 OCR 是有明确定义的安全态（无码票走的就是这条路），
    而绑错税局单据是无界的数据污染 —— 两者不对称，所以宁可不绑。
    """
    page = getattr(ref, "page", None)
    if page is None:
        return False
    pages = entity.get("page")
    if isinstance(pages, (int, float)):
        pages = [int(pages)]
    if not isinstance(pages, list) or not pages:
        return False
    try:
        return int(page) not in {int(x) for x in pages}
    except (TypeError, ValueError):
        return False


def _merge_one(
    entity: dict, mapped: dict, source: str, prov: dict[str, str]
) -> list[str]:
    """把 mapped 覆盖进 entity（高优先级源调用在后）。返回被覆盖的字段名。"""
    overridden: list[str] = []

    for key, value in mapped.items():
        if key == "_myinvois" or key in _NEVER_OVERRIDE:
            continue
        if _is_empty(value):
            continue                              # 该源没有 → 保留低优先级值
        if key == "detailOfGoodsOrServices":
            merged = _merge_lines(entity.get(key), value)
            if merged is not None:
                entity[key] = merged
                prov[key] = source
                overridden.append(key)
            continue
        entity[key] = value
        prov[key] = source
        overridden.append(key)

    _apply_credit_note_signs(entity)
    return overridden


def _merge_lines(ocr_lines: Any, hi_lines: list[dict]) -> list[dict] | None:
    """行项目合并：高优先级源权威 + 保留低优先级的行级单号。"""
    if not isinstance(hi_lines, list) or not hi_lines:
        return None
    low = ocr_lines if isinstance(ocr_lines, list) else []
    out: list[dict] = []
    positional = len(low) == len(hi_lines)
    for i, ln in enumerate(hi_lines):
        row = dict(ln)
        if positional and isinstance(low[i], dict):
            for k in _LINE_OCR_ONLY:
                if _is_empty(row.get(k)) and not _is_empty(low[i].get(k)):
                    row[k] = low[i][k]
        out.append(row)
    return out


def _apply_credit_note_signs(entity: dict) -> None:
    """Credit Note 金额取负 —— UBL 只存绝对值，符号靠类型码表达。"""
    if str(entity.get("invoiceType") or "").strip() != "Credit Note":
        return

    for k in _SIGNED_TOTALS:
        v = entity.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            entity[k] = -v

    lines = entity.get("detailOfGoodsOrServices")
    if isinstance(lines, list):
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            for k in _SIGNED_LINE:
                v = ln.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                    ln[k] = -v

    for row in entity.get("detailOfTaxSummary") or []:
        if not isinstance(row, dict):
            continue
        for k in ("tax", "netTaxableAmount"):
            v = row.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                row[k] = -v
