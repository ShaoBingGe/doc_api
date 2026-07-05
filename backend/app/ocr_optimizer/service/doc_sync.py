"""文档 structured_data 同步（customer_iteration 拆分第三刀）.

定制/迭代结束后，把样本文档的持久化 OCR 输出对齐到「最终 prompt」：

  - `_rewrite_all_docs_structured_data` —— Phase 23.3：定制 fork 后按 rename
    映射改写 structured_data 顶层键（消除「JSON 显示旧名 / 模块列表新名」漂移）；
  - `_reocr_all_docs_with_active_prompt` —— Phase 25：3 轮迭代 finalize 后，
    用最终激活 prompt 重跑全部样本 OCR，让每个 doc 的 JSON 输出面板字段集一致。

两者都是纯 DB / 文档同步，与迭代主逻辑无关。GT 安全：reprocess 只产生新版
ai_detected 标注（is_corrected=False），不删旧标注、不把 OCR 输出升为 GT。
函数名保持原样（含下划线），customer_iteration 作 facade 重导出。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _rewrite_all_docs_structured_data(
    db: Session,
    api_def_id: uuid.UUID,
    renames: dict[str, str],
) -> int:
    """Phase 23.3 post-customize sweep.

    For every ProcessingResult on every Document bound to api_def_id,
    rewrite top-level structured_data keys per the renames map. Used
    immediately after _fork_api_definition so the workspace's cached
    OCR outputs match the new module key names — eliminating the
    "JSON shows old name / module list shows new name" drift.

    Returns the number of ProcessingResult rows touched.
    """
    if not renames:
        return 0
    from app.domain.extraction_pipeline import rewrite_structured_data_keys as _rewrite_structured_data_keys
    from app.models.document import Document as _Document, ProcessingResult as _PR

    doc_ids = [d.id for d in db.query(_Document.id)
               .filter(_Document.api_definition_id == api_def_id).all()]
    if not doc_ids:
        return 0

    rows = db.query(_PR).filter(_PR.document_id.in_(doc_ids)).all()
    touched = 0
    for pr in rows:
        if not pr.structured_data:
            continue
        new_sd = _rewrite_structured_data_keys(pr.structured_data, renames)
        if new_sd != pr.structured_data:
            pr.structured_data = new_sd
            touched += 1
    db.commit()
    logger.info(
        "Phase 23.3: rewrote structured_data on %d ProcessingResult rows "
        "across %d docs of ApiDef %s (renames=%d)",
        touched, len(doc_ids), api_def_id, len(renames),
    )
    return touched


def _reocr_all_docs_with_active_prompt(
    db: Session,
    api_def_id: uuid.UUID,
) -> tuple[int, int]:
    """Phase 25 — after the 3-round iteration finalizes (the final prompt is
    now the ACTIVE OcrPromptVersion), re-run OCR on every sample document of
    the ApiDef so each doc's persisted ProcessingResult.structured_data
    reflects the SAME final prompt.

    Why this is needed
    ------------------
    During the 3 rounds, `run_orchestrator._run_one_round` OCRs the samples
    only to *evaluate* accuracy — the output lives on
    OcrOptimizationRound.ocr_raw_outputs and is never written back to the
    documents. `finalize_run` only flips the active-version pointer. So each
    doc's structured_data still reflects whatever prompt last extracted IT
    (initial upload, the augmented new-sample upload, a manual retry) — which
    differs per doc. The workspace's middle field column papers over this with
    a client-side rename overlay, but the JSON output panel reads the raw
    per-doc structured_data and exposes the drift: one file shows 38 fields
    with `billFromName`, another shows 14 fields with `salerName`, etc.

    Phase 23.3's `_rewrite_all_docs_structured_data` only renames TOP-LEVEL
    keys — it can't reconcile genuinely different field SETS / nesting / new
    fields. A real re-extraction with the unified final prompt is the only
    fix that makes every doc's JSON output consistent.

    GT safety (CLAUDE.md invariants)
    --------------------------------
    `reprocess_document` creates a NEW ProcessingResult version with fresh
    `ai_detected` annotations (is_corrected=False) and does NOT delete prior
    annotations. `ground_truth.build` reads is_corrected/manual annotations
    across ALL versions, so the customer's confirmed GT survives untouched
    and no OCR output is auto-promoted to GT.

    Graceful degradation: a per-doc OCR failure marks that doc failed and is
    skipped; the job still completes. Returns (succeeded, failed).
    """
    from app.models.document import Document, DocumentStatus
    from app.schemas.document import ReprocessRequest
    from app.services.document_service import reprocess_document

    docs = (
        db.query(Document)
        .filter(Document.api_definition_id == api_def_id)
        .all()
    )
    succeeded = failed = 0
    for doc in docs:
        if not doc.storage_path:
            continue
        try:
            # prompt=None → reprocess_document resolves the ApiDef's ACTIVE
            # composed_prompt, which is the just-finalized final version.
            reprocess_document(db, doc.id, ReprocessRequest(prompt=None))
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Phase 25 re-OCR failed for doc=%s of ApiDef %s — %s",
                doc.id, api_def_id, exc,
            )
            # _run_extraction set status=failed + re-raised without commit;
            # roll back its partial txn, then persist the failure marker.
            db.rollback()
            d = db.get(Document, doc.id)
            if d:
                d.status = DocumentStatus.failed
                d.error_message = (str(exc) or "post-iteration re-OCR failed")[:1024]
                db.commit()
            failed += 1
    logger.info(
        "Phase 25: re-OCR'd %d/%d docs of ApiDef %s with final active prompt "
        "(%d failed)",
        succeeded, succeeded + failed, api_def_id, failed,
    )
    return succeeded, failed
