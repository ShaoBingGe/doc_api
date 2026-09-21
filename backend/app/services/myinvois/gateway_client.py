"""国际电子发票平台（piaozone/kdfpy）网关客户端 —— 马来西亚税局查验。

不直连 LHDN，统一经网关中转。相对直连 LHDN 的关键好处：网关的
`GET /v1/invoices/verify` **按 uuid 点查**并直接返回税局原始报文
（`invoiceDocument`），因此不需要"先按时间窗拉列表拿 documentId、再下载
Target 文件"那一套，也就不需要本地索引表与增量同步作业。

鉴权（与 LHDN 的 client_credentials 不同，是签名式）：
    sign = MD5(client_id + client_secret + timestamp)   小写十六进制
    POST /base/oauth/token  {client_id, timestamp, sign}  → access_token
    业务请求头：Authorization: <token 原值，不加 Bearer>
               X-Company-Id: <企业 ouno>

实测踩到的坑（文档与实现不符，已在此处按实现为准）：
  • 成功判据是 `errorcode == "200"`，**不是文档早期写的 "0000"**
  • 列表接口的时间必须是 ISO `2026-09-01T00:00:00`；带空格的
    `yyyy-MM-dd HH:mm:ss` 会被拒（Invalid datetime format）
  • 文件下载路径是小写 `/v1/documents/...`，不是文档写的大写 `Documents`
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 网关把「成功」写成 200；早期文档写的是 0000，两个都认
_OK_CODES = {"200", "0000"}


@dataclass
class VerifyResult:
    """`GET /v1/invoices/verify` 的结构化结果。"""

    ok: bool
    uuid: str
    verify_status: str | None = None      # VALID/INVALID/CANCELLED/PENDING/NOT_FOUND
    raw_status: str | None = None         # 税局原始状态（Valid/Invalid/...）
    document_no: str | None = None        # = MyInvois internalId
    verify_code: str | None = None        # = MyInvois longId
    submitted_at: str | None = None
    issued_at: str | None = None
    validated_at: str | None = None
    cancelled_at: str | None = None
    invoice_document: str | None = None   # 税局原始报文（XML 或 JSON 原文）
    invoice_document_format: str | None = None
    errorcode: str | None = None
    http_status: int | None = None
    reason: str | None = None

    @property
    def is_valid(self) -> bool:
        return (self.verify_status or "").upper() == "VALID"

    @property
    def has_document(self) -> bool:
        return bool(self.invoice_document and self.invoice_document.strip())

    def as_dict(self) -> dict:
        """给报告/日志用；**不含**报文正文（可能很大且含业务数据）。"""
        return {
            "uuid": self.uuid,
            "ok": self.ok,
            "verify_status": self.verify_status,
            "raw_status": self.raw_status,
            "document_no": self.document_no,
            "has_verify_code": bool(self.verify_code),
            "validated_at": self.validated_at,
            "cancelled_at": self.cancelled_at,
            "document_format": self.invoice_document_format,
            "document_chars": len(self.invoice_document or ""),
            "errorcode": self.errorcode,
            "http_status": self.http_status,
            "reason": self.reason,
        }


@dataclass
class _Token:
    value: str
    expires_at: float

    @property
    def alive(self) -> bool:
        # 留 60s 余量，避免边界上用到刚过期的 token
        return bool(self.value) and time.time() < self.expires_at - 60


class MyInvoisGateway:
    """网关客户端。token 进程内缓存并自动续签，线程安全。"""

    _lock = threading.Lock()
    # 按 (base_url, client_id, company_id) 分键。早先是单个类属性，
    # 换一组凭证（如 SIT ↔ 生产）会把上一组的 token 串给下一组，
    # 测试得靠手工 reset_token() 兜着 —— 那本身就是这条缺陷的证据。
    _tokens: dict[tuple[str, str, str], _Token] = {}

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        company_id: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        # None = 回退到配置；空串 = **显式**未配置（不要用 `or`，否则调用方
        # 无法表达"我就是要一个没配凭证的客户端"，测试也就守不住降级路径）。
        s = get_settings()
        def _pick(explicit: str | None, key: str) -> str:
            return explicit if explicit is not None else (getattr(s, key, "") or "")
        self.base = _pick(base_url, "MYINVOIS_GATEWAY_BASE_URL").rstrip("/")
        self.client_id = _pick(client_id, "MYINVOIS_GATEWAY_CLIENT_ID")
        self.client_secret = _pick(client_secret, "MYINVOIS_GATEWAY_CLIENT_SECRET")
        self.company_id = _pick(company_id, "MYINVOIS_GATEWAY_COMPANY_ID")
        self.timeout = timeout if timeout is not None else float(
            getattr(s, "MYINVOIS_TIMEOUT_S", 15.0))
        self._http = client
        self._cred_key = (self.base, self.client_id, self.company_id)

    # ── 配置与鉴权 ─────────────────────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self.base and self.client_id and self.client_secret and self.company_id)

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        if self._http is not None:
            return self._http.request(method, url, timeout=self.timeout, **kw)
        with httpx.Client(timeout=self.timeout) as c:
            return c.request(method, url, **kw)

    def _mint_token(self) -> str | None:
        ts = str(int(time.time() * 1000))
        sign = hashlib.md5(
            (self.client_id + self.client_secret + ts).encode()).hexdigest()
        try:
            r = self._request(
                "POST", f"{self.base}/base/oauth/token",
                headers={"Content-Type": "application/json"},
                json={"client_id": self.client_id, "timestamp": ts, "sign": sign},
            )
            j = r.json()
        except Exception as exc:
            logger.warning("网关换 token 失败：%s", exc)
            return None
        if str(j.get("errorcode")) not in _OK_CODES or not j.get("access_token"):
            logger.warning("网关换 token 被拒：errorcode=%s msg=%s",
                           j.get("errorcode"), j.get("message"))
            return None
        ttl = int(j.get("expires_in") or 3600)
        with self._lock:
            MyInvoisGateway._tokens[self._cred_key] = _Token(
                j["access_token"], time.time() + ttl)
        logger.info("网关 token 已签发，有效期 %ss", ttl)
        return j["access_token"]

    def token(self, *, force: bool = False) -> str | None:
        with self._lock:
            tk = MyInvoisGateway._tokens.get(self._cred_key)
        if not force and tk and tk.alive:
            return tk.value
        return self._mint_token()

    @classmethod
    def reset_token(cls) -> None:
        with cls._lock:
            cls._tokens.clear()

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": token,          # 原值，不加 Bearer
            "X-Company-Id": self.company_id,
            "X-Timezone": "Asia/Kuala_Lumpur",
            "Accept": "application/json",
        }

    # ── 业务接口 ───────────────────────────────────────────────────────────
    def verify(self, doc_uuid: str) -> VerifyResult:
        """按 uuid 查验，并取回税局原始报文。

        这是整条查验链路的**唯一必需调用** —— 它同时给出查验结论、时间戳与
        `invoiceDocument`（UBL 原文），后者交给 ubl_mapper 展开成 29 字段子集。
        """
        if not self.configured:
            return VerifyResult(ok=False, uuid=doc_uuid, reason="网关凭证未配置")

        for attempt in (0, 1):                       # 401 时刷新 token 重试一次
            tok = self.token(force=bool(attempt))
            if not tok:
                return VerifyResult(ok=False, uuid=doc_uuid, reason="无法获取网关 token")
            try:
                r = self._request(
                    "GET", f"{self.base}/v1/invoices/verify",
                    headers=self._headers(tok),
                    params={"taxDocumentId": doc_uuid},
                )
            except Exception as exc:
                logger.info("网关查验请求失败 uuid=%s: %s", doc_uuid, exc)
                return VerifyResult(ok=False, uuid=doc_uuid, reason=f"transport: {exc}")

            if r.status_code == 401 and attempt == 0:
                MyInvoisGateway.reset_token()
                continue
            return self._parse_verify(r, doc_uuid)
        return VerifyResult(ok=False, uuid=doc_uuid, reason="鉴权重试后仍失败")

    def _parse_verify(self, r: httpx.Response, doc_uuid: str) -> VerifyResult:
        try:
            j = r.json()
        except Exception:
            return VerifyResult(ok=False, uuid=doc_uuid, http_status=r.status_code,
                                reason=f"响应非 JSON: {(r.text or '')[:120]}")
        code = str(j.get("errorcode"))
        if code not in _OK_CODES:
            return VerifyResult(
                ok=False, uuid=doc_uuid, errorcode=code, http_status=r.status_code,
                reason=j.get("message") or f"errorcode={code}")
        d = j.get("data") or {}
        if not isinstance(d, dict):
            return VerifyResult(ok=False, uuid=doc_uuid, errorcode=code,
                                http_status=r.status_code, reason="data 为空")
        return VerifyResult(
            ok=True, uuid=doc_uuid, errorcode=code, http_status=r.status_code,
            verify_status=d.get("verifyStatus"),
            raw_status=d.get("rawStatus"),
            document_no=d.get("documentNo"),
            verify_code=d.get("verifyCode"),
            submitted_at=d.get("submittedAt"),
            issued_at=d.get("issuedAt"),
            validated_at=d.get("validatedAt"),
            cancelled_at=d.get("cancelledAt"),
            invoice_document=d.get("invoiceDocument"),
            invoice_document_format=d.get("invoiceDocumentFormat"),
        )

    def list_documents(
        self, *, direction: str = "Received", start: str, end: str,
        document_type: str = "Invoice", cursor: int = 0, limit: int = 500,
    ) -> dict:
        """进项/销项列表。查验链路用不到，保留给对账/批量同步场景。

        时间必须是 ISO `2026-09-01T00:00:00`（带空格的格式会被网关拒绝）。
        """
        tok = self.token()
        if not tok:
            return {"errorcode": "401", "message": "无法获取网关 token", "data": None}
        qs = urlencode({
            "startUpdateTime": start, "endUpdateTime": end,
            "documentType": document_type, "cursor": cursor, "limit": limit,
        }, quote_via=quote)
        try:
            r = self._request(
                "GET", f"{self.base}/v1/clearance/documents/{direction}?{qs}",
                headers=self._headers(tok))
            return r.json()
        except Exception as exc:
            return {"errorcode": "500", "message": str(exc), "data": None}
