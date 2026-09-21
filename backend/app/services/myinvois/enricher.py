"""编排：PDF 二维码 → 网关查验 → 与 OCR 消融。

固定两条分支（无策略开关）：

    有二维码 ──► uuid ──► 网关 /v1/invoices/verify
                          ├─ 查验结论（VALID/CANCELLED/…）+ 时间戳
                          └─ invoiceDocument（税局原始 UBL）
                          ↓ ubl_mapper 展开
                          ↓ 与 OCR 消融（税局恒优先）
    无二维码 ──► 纯 OCR，原样返回（零额外开销）

为什么以二维码为闸门（chinkin 108 份实测）：
    二维码覆盖 80 份 74.1%；链接注记/文字层方法只有 49 份 45.4%，
    且后者能提到的二维码**全都覆盖**（0 份仅靠文字层）。二维码还额外
    带 share token（= longId），文字层没有。

查验阶段（二维码解码 + 网关调用）**完全不依赖 OCR 输出**，因此拆成两段：

    lookup_myinvois(file_bytes)   ← 可与 OCR 并行跑
    fuse_lookup(entities, lookup) ← OCR 出结果后再合

extract_service 用这个拆分把查验丢进线程池，与模型调用重叠，省掉串行等待。
`enrich_and_fuse` 保留为顺序版便捷入口（测试与非并行调用方仍可用）。

整链 fail-open：任何环节异常只落进报告，OCR 结果照常返回。
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, field
import uuid as uuid_mod
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings

from .fusion import FusionReport, fuse_sources
from .gateway_client import MyInvoisGateway, VerifyResult
from .qr_extractor import QrHit, extract_qr_hits
from .ubl_mapper import map_document

logger = logging.getLogger(__name__)

# 网关 verifyStatus → 是否可作为权威值覆盖 OCR
_AUTHORITATIVE = {"VALID"}


def _mapped_from_verify(hit: QrHit, res: VerifyResult) -> dict | None:
    """把一次查验结果展开成 29 字段子集 + 元信息。

    UBL 正文给业务字段；查验回执补上 UBL 本身不承载的三样东西 ——
    查验状态、税局收到时间、税局验证时间（客户要的 8 要素里的后两个）。
    """
    mapped: dict[str, Any] = {}
    if res.has_document:
        mapped = map_document({
            "document": res.invoice_document,
            "uuid": res.uuid,
            "status": res.raw_status,
            "internalId": res.document_no,
            "longId": res.verify_code,
        }) or {}

    if not mapped:
        # 没有报文（或解不出）也别浪费回执：至少把发票号落下来
        mapped = {}
        if res.document_no:
            mapped["invoiceNumber"] = res.document_no

    meta = dict(mapped.get("_myinvois") or {})
    meta.update({
        "uuid": res.uuid,
        "status": res.raw_status or res.verify_status,
        "verify_status": res.verify_status,
        "document_no": res.document_no,
        "long_id": res.verify_code or hit.long_id or None,
        "submitted_at": res.submitted_at,      # 发送日期
        "issued_at": res.issued_at,
        "validated_at": res.validated_at,      # 校验日期
        "cancelled_at": res.cancelled_at,
        "document_format": res.invoice_document_format,
        "qr_page": hit.page,
        "qr_method": hit.method,
    })
    mapped["_myinvois"] = meta
    return mapped if (len(mapped) > 1) else None


@dataclass
class MyInvoisLookup:
    """查验阶段的产物 —— 与 OCR 无关，可提前并行算好。"""

    pairs: list[tuple[Any, dict]] = field(default_factory=list)
    report: FusionReport = field(default_factory=FusionReport)


def lookup_myinvois(
    file_bytes: bytes, *, gateway: MyInvoisGateway | None = None
) -> MyInvoisLookup:
    """二维码 → 网关查验 → 映射。**不碰 OCR 结果**，故可与模型调用并行。"""
    settings = get_settings()
    out = MyInvoisLookup()
    report = out.report

    if not bool(getattr(settings, "MYINVOIS_ENABLED", False)):
        report.enabled = False
        return out

    try:
        hits = extract_qr_hits(
            file_bytes,
            max_pages=int(getattr(settings, "MYINVOIS_SCAN_MAX_PAGES", 16)) or None,
            budget_s=float(getattr(settings, "MYINVOIS_SCAN_BUDGET_S", 12.0)),
        )
    except Exception as exc:
        logger.warning("二维码识别异常：%s", exc, exc_info=True)
        report.error = f"二维码识别失败: {exc}"
        return out

    report.refs_found = len(hits)
    if not hits:
        return out                          # 无码 → 纯 OCR，正常路径
    report.links = [h.as_dict() for h in hits]

    gw = gateway or MyInvoisGateway()
    if not gw.configured:
        report.error = "网关凭证未配置，仅走 OCR"
        return out

    max_lookups = int(getattr(settings, "MYINVOIS_MAX_LOOKUPS", 12))
    budget_s = float(getattr(settings, "MYINVOIS_BUDGET_S", 30.0))
    started = time.monotonic()

    # 复用连接省掉逐次握手；但**绝不覆盖**构造时注入的 client ——
    # 那会让 `MyInvoisGateway(client=MockTransport(...))` 真的发出网络请求，
    # 且多线程共享同一 gateway 时会撞上「client 已关闭」。
    injected = gw._http is not None
    try:
        with contextlib.ExitStack() as stack:
            if not injected:
                http = stack.enter_context(httpx.Client(timeout=gw.timeout))
                gw._http = http
            for i, hit in enumerate(hits):
                if i >= max_lookups:
                    report.skipped.append({
                        "uuid": hit.uuid, "page": hit.page, "action": "skipped",
                        "reason": f"超过单次查验条数上限 {max_lookups}"})
                    continue
                if time.monotonic() - started > budget_s:
                    report.skipped.append({
                        "uuid": hit.uuid, "page": hit.page, "action": "skipped",
                        "reason": f"超过查验时间预算 {budget_s}s"})
                    continue

                res = gw.verify(hit.uuid)
                report.fetched += 1
                if not res.ok:
                    report.skipped.append({
                        "uuid": hit.uuid, "page": hit.page, "action": "verify_failed",
                        "errorcode": res.errorcode, "http_status": res.http_status,
                        "reason": res.reason})
                    continue

                status = (res.verify_status or "").upper()
                if status not in _AUTHORITATIVE and bool(
                        getattr(settings, "MYINVOIS_REQUIRE_VALID", True)):
                    # 已作废/无效/待处理不该覆盖 OCR —— 但必须上报，
                    # 「税局说这张票已取消」本身就是高价值业务信号。
                    report.skipped.append({
                        "uuid": hit.uuid, "page": hit.page, "action": "reported_only",
                        "verify_status": res.verify_status,
                        "raw_status": res.raw_status,
                        "cancelled_at": res.cancelled_at,
                        "reason": f"查验状态为 {res.verify_status}，不覆盖 OCR"})
                    continue

                mapped = _mapped_from_verify(hit, res)
                if not mapped:
                    report.skipped.append({
                        "uuid": hit.uuid, "page": hit.page, "action": "map_failed",
                        "reason": "税局报文解析/映射未产出可用字段"})
                    continue
                out.pairs.append((hit, mapped))
    except Exception as exc:
        logger.warning("网关查验阶段异常：%s", exc, exc_info=True)
        report.error = f"查验失败: {exc}"
    finally:
        if not injected:                # 注入的 client 归调用方所有，不能替它清掉
            gw._http = None

    return out


def fuse_lookup(
    entities: list[dict], lookup: MyInvoisLookup
) -> tuple[list[dict], FusionReport]:
    """把查验结果消融进 OCR 票据（税局恒优先）。"""
    base = lookup.report
    if not base.enabled or not lookup.pairs:
        return entities, base
    if not entities:
        base.error = "OCR 未产出票据，无可消融的基底"
        return entities, base

    try:
        fused, fuse_report = fuse_sources(
            entities, [("lhdn", lookup.pairs)],
            require_valid_status=False,     # 状态门已在 lookup 阶段按 verifyStatus 判过
        )
    except Exception as exc:
        logger.warning("消融阶段异常：%s", exc, exc_info=True)
        base.error = f"融合失败: {exc}"
        return entities, base

    fuse_report.refs_found = base.refs_found
    fuse_report.fetched = base.fetched
    fuse_report.links = base.links
    fuse_report.skipped = base.skipped + fuse_report.skipped
    fuse_report.error = base.error
    return fused, fuse_report


def enrich_and_fuse(
    db: Session | None = None,
    *,
    file_bytes: bytes,
    entities: list[dict],
    tenant_id: uuid_mod.UUID | None = None,
    gateway: MyInvoisGateway | None = None,
) -> tuple[list[dict], FusionReport]:
    """顺序版便捷入口：查验 + 消融一把做完。

    并行路径请改用 `lookup_myinvois` + `fuse_lookup`（见 extract_service）。
    """
    lookup = lookup_myinvois(file_bytes, gateway=gateway)
    return fuse_lookup(entities, lookup)
