"""MyInvois（马来西亚税局）查验：健康检查与自检端点。

凭证模型已变更：平台持有网关的 client_id/secret，**自行签名换取 token**
（sign = MD5(client_id+client_secret+timestamp)），不再需要外部定时推送
access_token —— 原 `PUT /myinvois/token` 一族端点因此退役。

  GET  /api/v1/myinvois/health   网关配置与连通性自检（不回显任何密钥）
  POST /api/v1/myinvois/probe    上传 PDF 自检：能解出哪些二维码、能否查到数
"""

from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.services.myinvois import (
    MyInvoisGateway,
    extract_qr_hits,
    map_document,
)

router = APIRouter(
    prefix="/myinvois",
    tags=["MyInvois (LHDN)"],
    dependencies=[Depends(get_current_user)],
)


class HealthResponse(BaseModel):
    enabled: bool
    configured: bool
    base_url: str | None = None
    company_id_masked: str | None = None
    token_ok: bool | None = None
    detail: str | None = None


@router.get("/health", response_model=HealthResponse)
def health():
    """确认网关凭证是否配好、能否换到 token。绝不回显 secret 或 token。"""
    s = get_settings()
    gw = MyInvoisGateway()
    if not bool(getattr(s, "MYINVOIS_ENABLED", False)):
        return HealthResponse(enabled=False, configured=gw.configured,
                              detail="MYINVOIS_ENABLED=false，查验增强未启用")
    if not gw.configured:
        return HealthResponse(enabled=True, configured=False,
                              detail="网关凭证未配置（base_url / client_id / secret / company_id）")
    cid = gw.company_id or ""
    tok = gw.token()
    return HealthResponse(
        enabled=True, configured=True, base_url=gw.base,
        company_id_masked=f"{cid[:4]}…{cid[-4:]}" if len(cid) > 8 else "…",
        token_ok=bool(tok),
        detail="网关可用" if tok else "凭证已配置但换 token 失败",
    )


@router.post("/probe")
async def probe(file: UploadFile = File(..., description="待自检的 PDF")):
    """自检：这份 PDF 能解出哪些税局二维码？能否用网关查到数？

    只读、不写库、不跑 OCR —— 接线阶段确认链路是否通。
    """
    content = await file.read()
    return await anyio.to_thread.run_sync(_probe_sync, content, file.filename)


def _probe_sync(content: bytes, filename: str | None) -> dict:
    """自检的同步实现 —— **必须**经 `to_thread` 调用，不能直接 await。

    二维码扫描是纯 CPU 的整页渲染，`gw.verify()` 是阻塞 HTTP（单次最长
    MYINVOIS_TIMEOUT_S）。在 async 路由里直接跑等于独占事件循环：取 token、
    异步任务轮询、健康检查会一并假死 —— 正是 CLAUDE.md 红线⑦记录的那起事故
    （腾讯云 200 秒识别期间取 token 30 秒超时）。对照组是本模块的 `health()`，
    它是普通 `def`，FastAPI 自动丢线程池，写法正确。
    """
    s = get_settings()
    hits = extract_qr_hits(
        content,
        max_pages=int(getattr(s, "MYINVOIS_SCAN_MAX_PAGES", 16)) or None,
        budget_s=float(getattr(s, "MYINVOIS_SCAN_BUDGET_S", 12.0)),
    )
    out: dict = {
        "filename": filename,
        "qr_found": len(hits),
        "qr": [h.as_dict() for h in hits],
        "lookups": [],
    }
    if not hits:
        out["note"] = "票面无税局二维码 → 按规则直接走纯 OCR"
        return out

    gw = MyInvoisGateway()
    if not gw.configured:
        out["note"] = "网关凭证未配置，仅做二维码解码"
        return out

    max_lookups = int(getattr(s, "MYINVOIS_MAX_LOOKUPS", 12))
    for i, h in enumerate(hits):
        if i >= max_lookups:          # 与 lookup_myinvois 同一把刹车，自检不该比生产路径更放纵
            out.setdefault("skipped", []).append(
                {"uuid": h.uuid, "page": h.page,
                 "reason": f"超过单次查验条数上限 {max_lookups}"})
            continue
        res = gw.verify(h.uuid)
        item = res.as_dict()
        item["qr_page"] = h.page
        if res.ok and res.has_document:
            m = map_document({
                "document": res.invoice_document, "uuid": res.uuid,
                "status": res.raw_status, "internalId": res.document_no,
                "longId": res.verify_code,
            }) or {}
            m.pop("_myinvois", None)
            item["mapped_fields"] = sorted(m.keys())
            item["mapped_preview"] = {
                k: m[k] for k in ("invoiceNumber", "invoiceDate", "currency",
                                  "billFromName", "billFromTaxIdentificationNumber",
                                  "billToName", "billToTaxIdentificationNumber",
                                  "totalNetAmount", "totalTaxAmount", "totalAmount")
                if k in m
            }
        out["lookups"].append(item)
    return out
