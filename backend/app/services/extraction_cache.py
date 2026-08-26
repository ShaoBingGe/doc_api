"""提取结果缓存的读写。

命中时**必须改写两个字段**再返回：
  * `traceId` —— 缓存的是识别结果，不是那一次请求；沿用旧 traceId 会让
    两次不同的请求在日志里指向同一条链路，排障时直接指错方向。
  * `sourceFileHash` —— 回填**本次**调用方传的 fileHash（他们常常不传，
    那就留空），不能把上一次的值带出来。

失败一律 fail-open：缓存出任何问题都退回正常识别，绝不让缓存成为新的故障源。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.extraction_cache import ExtractionCache

logger = logging.getLogger(__name__)


def content_hash(file_bytes: bytes) -> str:
    """文件内容的 sha256。服务端自算，不依赖调用方的 fileHash。"""
    return hashlib.sha256(file_bytes).hexdigest()


def _key(*, client_id: str, template_id: str, chash: str) -> str:
    """键不含 prompt 版本 —— TTL 只有 15 分钟，版本漂移的风险窗口足够小。
    若 TTL 调长到小时级，必须把版本加回来（见模型层说明）。"""
    raw = f"{client_id}|{template_id}|{chash}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _retrace(payload: dict, *, trace_id: str, source_file_hash: str) -> dict:
    """把缓存结果改写成"属于本次请求"的样子。"""
    out = copy.deepcopy(payload)
    out["traceId"] = trace_id
    for entity in out.get("data") or []:
        basic = (entity.get("header") or {}).get("basic")
        if isinstance(basic, dict) and "sourceFileHash" in basic:
            basic["sourceFileHash"] = source_file_hash or ""
    return out


def lookup(
    db: Session,
    *,
    client_id: str,
    template_id: str,
    chash: str,
    trace_id: str,
    source_file_hash: str = "",
) -> tuple[dict, int] | None:
    """→ (改写后的响应体, doc_pages)；未命中/已过期/异常返回 None。"""
    s = get_settings()
    if not s.EXTRACT_CACHE_ENABLED:
        return None
    try:
        row = db.get(ExtractionCache, _key(
            client_id=client_id, template_id=template_id, chash=chash))
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        exp = row.expires_at
        if exp is not None and (exp if exp.tzinfo else exp.replace(
                tzinfo=timezone.utc)) <= now:
            return None                    # 过期不算命中，交给清理去删

        payload = json.loads(row.result_json)
        row.hits += 1
        row.last_hit_at = now
        db.commit()
        logger.info(
            "缓存命中 client=%s template=%s hash=%s… 第 %d 次复用（省一次模型调用）",
            client_id, template_id, chash[:12], row.hits,
        )
        return _retrace(payload, trace_id=trace_id,
                        source_file_hash=source_file_hash), row.doc_pages
    except Exception:  # noqa: BLE001 — 缓存永远不该让主流程失败
        logger.warning("缓存读取失败，退回正常识别", exc_info=True)
        db.rollback()
        return None


def store(
    db: Session,
    *,
    client_id: str,
    template_id: str,
    chash: str,
    payload: dict,
    doc_pages: int,
) -> None:
    """写入结果。只存成功的（errcode=0000）；失败结果重试可能会成功。"""
    s = get_settings()
    if not s.EXTRACT_CACHE_ENABLED:
        return
    if (payload or {}).get("errcode") != "0000":
        return
    try:
        now = datetime.now(timezone.utc)
        key = _key(client_id=client_id, template_id=template_id, chash=chash)
        row = db.get(ExtractionCache, key)
        if row is None:
            row = ExtractionCache(
                id=key, client_id=client_id, template_id=str(template_id),
                content_hash=chash,
                result_json=json.dumps(payload, ensure_ascii=False),
                doc_pages=doc_pages,
                expires_at=now + timedelta(minutes=s.EXTRACT_CACHE_TTL_MIN),
            )
            db.add(row)
        else:
            row.result_json = json.dumps(payload, ensure_ascii=False)
            row.doc_pages = doc_pages
            row.expires_at = now + timedelta(minutes=s.EXTRACT_CACHE_TTL_MIN)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("缓存写入失败（不影响本次结果）", exc_info=True)
        db.rollback()


def purge_expired(db: Session) -> int:
    """删除过期缓存行。→ 删除条数。"""
    try:
        now = datetime.now(timezone.utc)
        rows = db.query(ExtractionCache).filter(
            ExtractionCache.expires_at <= now).all()
        for r in rows:
            db.delete(r)
        db.commit()
        if rows:
            logger.info("清理过期结果缓存 %d 条", len(rows))
        return len(rows)
    except Exception:  # noqa: BLE001
        logger.warning("清理结果缓存失败", exc_info=True)
        db.rollback()
        return 0
