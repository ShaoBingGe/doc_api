"""
ExtractService — 公有 API 文档提取核心逻辑。

调用链：
  API Key 认证 → 查 ApiDefinition → 保存临时文件 → 调用 Processor → 返回结构化数据

支持三种文件来源：
  1. multipart/form-data  file 字段
  2. JSON body { file_url: "https://..." }
  3. JSON body { file_base64: "data:...;base64,..." }
"""

from __future__ import annotations

import base64
import time
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    ApiDeprecatedError,
    FileTooLargeError,
    NotFoundError,
    ProcessingError,
    UnsupportedFileTypeError,
)
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.api_key import ApiKey
from app.models.document import Document, DocumentStatus, ProcessingResult
from app.models.usage_record import UsageRecord
from app.schemas.extract import ExtractMetadata, ExtractResponse

settings = get_settings()

_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx"}


def extract_document(
    db: Session,
    *,
    api_code: str,
    api_key: ApiKey | None = None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    file_url: str | None = None,
    file_base64: str | None = None,
    request_ip: str = "unknown",
) -> ExtractResponse:
    """
    Main extraction entry point.

    1. Resolve file bytes from one of the three sources
    2. Validate api_code and ApiDefinition status
    3. Run AI extraction via ProcessorFactory
    4. Persist Document + ProcessingResult for audit trail
    5. Return structured response
    """
    request_id = uuid.uuid4()

    # ── 1. Resolve file ───────────────────────────────────────────────────
    if file_bytes is None:
        if file_url:
            file_bytes, filename = _fetch_from_url(file_url)
        elif file_base64:
            file_bytes, filename = _decode_base64(file_base64)
        else:
            raise ProcessingError("No file provided: use multipart file, file_url, or file_base64")

    if len(file_bytes) > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"File size {len(file_bytes) / 1024 / 1024:.1f} MB exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit"
        )

    safe_filename = filename or "upload.bin"
    _validate_extension(safe_filename)

    # ── 2. Validate ApiDefinition ─────────────────────────────────────────
    api_def = db.query(ApiDefinition).filter(ApiDefinition.api_code == api_code).first()
    if not api_def:
        raise NotFoundError(f"api_code '{api_code}' not found", {"code": "api_not_found"})
    if api_def.status == ApiDefinitionStatus.deprecated:
        raise ApiDeprecatedError(f"API '{api_code}' has been deprecated")
    if api_def.status == ApiDefinitionStatus.draft:
        raise NotFoundError(f"api_code '{api_code}' is not yet active", {"code": "api_not_found"})

    # ── 3. Save temp file ─────────────────────────────────────────────────
    temp_path = _save_temp(file_bytes, safe_filename)

    # ── 4. Run extraction ─────────────────────────────────────────────────
    start_ms = int(time.time() * 1000)
    try:
        # Fetch active modular OCR prompt if available
        from app.ocr_optimizer import get_active_composed_prompt
        active_prompt = get_active_composed_prompt(db, api_def.id)

        # Availability-aware resolution（已发布 API 不能因行里钉死的 provider
        # 在当前环境不可达而识别失效 —— 与 reprocess_document 的 env-first
        # 契约一致；按模板 prompt 识别不变，仅换可用的执行引擎）。
        from app.processors.factory import ProcessorFactory
        proc_used, model_pref = ProcessorFactory.resolve_spec(
            api_def.processor_type, api_def.model_name
        )
        raw_output, structured_data, model_name, tokens_used = _run_processor(
            storage_path=temp_path,
            processor_type=proc_used,
            model_name=model_pref,
            prompt=active_prompt,
            schema=api_def.response_schema,
        )
    except Exception as exc:
        raise ProcessingError(f"Extraction failed: {exc}") from exc
    finally:
        _cleanup_temp(temp_path)

    elapsed_ms = int(time.time() * 1000) - start_ms

    # ── 5. Audit trail ────────────────────────────────────────────────────
    _persist_audit(
        db,
        api_def=api_def,
        api_key=api_key,
        filename=safe_filename,
        file_size=len(file_bytes),
        structured_data=structured_data,
        raw_output=raw_output,
        model_name=model_name,
        tokens_used=tokens_used,
        processing_time_ms=elapsed_ms,
        request_ip=request_ip,
        processor_used=proc_used,
    )

    # structured_data 可能是数组（多票据）或单对象；data 保持「首条 dict」的既有契约，
    # entities 给出全量，供开放平台等需要多票据的调用方使用。
    if isinstance(structured_data, list):
        entities = [e for e in structured_data if isinstance(e, dict)]
        first = entities[0] if entities else {}
    elif isinstance(structured_data, dict):
        inner = next((structured_data[k] for k in ("entities", "data")
                      if isinstance(structured_data.get(k), list)), None)
        entities = [e for e in inner if isinstance(e, dict)] if inner is not None \
            else [structured_data]
        first = entities[0] if entities else {}
    else:
        entities, first = [], {}

    return ExtractResponse(
        request_id=request_id,
        api_code=api_code,
        api_version=api_def.version,
        data=first,
        entities=entities,
        metadata=ExtractMetadata(
            processor=proc_used,
            model=model_name,
            tokens_used=tokens_used or 0,
            processing_time_ms=elapsed_ms,
        ),
    )


# ── File resolution helpers ───────────────────────────────────────────────────

def _fetch_from_url(url: str) -> tuple[bytes, str]:
    """Download a file from a public URL (max 20 MB)."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise FileTooLargeError("Remote file exceeds size limit")
        filename = Path(url.split("?")[0]).name or "remote_file"
        return data, filename
    except FileTooLargeError:
        raise
    except Exception as exc:
        raise ProcessingError(f"Failed to fetch file from URL: {exc}") from exc


def _decode_base64(data_uri: str) -> tuple[bytes, str]:
    """Decode a data URI: data:<mime>;base64,<data>"""
    try:
        header, _, b64 = data_uri.partition(",")
        file_bytes = base64.b64decode(b64)
        mime = header.split(";")[0].replace("data:", "")
        ext = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        }.get(mime, ".bin")
        return file_bytes, f"upload{ext}"
    except Exception as exc:
        raise ProcessingError(f"Invalid base64 data URI: {exc}") from exc


def _validate_extension(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"File type '{ext}' not supported. Allowed: {', '.join(_ALLOWED_EXTENSIONS)}"
        )


# ── Storage helpers ───────────────────────────────────────────────────────────

def _save_temp(file_bytes: bytes, filename: str) -> str:
    temp_dir = Path(settings.UPLOAD_DIR) / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe = f"{uuid.uuid4().hex}_{Path(filename).name}"
    dest = temp_dir / safe
    dest.write_bytes(file_bytes)
    return str(dest)


def _cleanup_temp(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


# ── Processor call ────────────────────────────────────────────────────────────

def _run_processor(
    storage_path: str,
    processor_type: str,
    model_name: str,
    prompt: str | None,
    schema: dict | None,
) -> tuple[dict, dict, str, int | None]:
    """Returns (raw_output, structured_data, actual_model_name, tokens_used)."""
    import json

    from app.processors.factory import ProcessorFactory

    processor = ProcessorFactory.create(processor_type, model_name=model_name)

    # Build instruction from schema or use provided prompt
    if prompt:
        instruction = prompt
    elif schema:
        instruction = (
            f"Extract structured data from this document according to the following JSON Schema. "
            f"Return ONLY valid JSON that conforms to the schema.\n\nSchema:\n{json.dumps(schema, ensure_ascii=False)}"
        )
    else:
        instruction = (
            "Extract all structured data from this document and return it as valid JSON."
        )

    raw_text = processor.process_document(storage_path, instruction)

    # Parse JSON from returned text
    try:
        structured_data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        from app.processors.base import extract_json
        blocks = extract_json(raw_text or "")
        if blocks:
            try:
                structured_data = json.loads(blocks[0])
            except (json.JSONDecodeError, TypeError):
                structured_data = {"raw": raw_text}
        else:
            structured_data = {"raw": raw_text}

    # 一份文档可能含多张票据（国家模板的 schema 根就是 ARRAY），此处**必须原样返回**。
    # 历史实现在这里做过 `structured_data = structured_data[0]` 的「归一」，
    # 把多票据文档的第 2 张起全部丢弃——6 页 6 张的 PDF 只会剩第 1 张。
    # 调用方按需取首条（见 extract_document 的 data 字段）或全量（entities 字段）。
    actual_model = processor.get_model_version()
    return {}, structured_data, actual_model, None


# ── Audit persistence ─────────────────────────────────────────────────────────

def _persist_audit(
    db: Session,
    *,
    api_def: ApiDefinition,
    api_key: ApiKey | None,
    filename: str,
    file_size: int,
    structured_data: dict,
    raw_output: dict,
    model_name: str,
    tokens_used: int | None,
    processing_time_ms: int,
    request_ip: str,
    processor_used: str | None = None,
) -> None:
    """Persist Document + ProcessingResult for audit trail. Non-fatal on error."""
    try:
        doc = Document(
            filename=filename,
            file_type=Path(filename).suffix.lstrip("."),
            file_size=file_size,
            storage_path="",  # temp file already deleted
            status=DocumentStatus.completed,
        )
        db.add(doc)
        db.flush()

        result = ProcessingResult(
            document_id=doc.id,
            version=1,
            processor_type=processor_used or api_def.processor_type,
            model_name=model_name,
            raw_output=raw_output,
            structured_data=structured_data,
            tokens_used=tokens_used,
            processing_time_ms=processing_time_ms,
        )
        db.add(result)
        db.commit()
    except Exception:
        db.rollback()  # audit failure must not break the response


def record_usage(
    db: Session,
    *,
    api_def: ApiDefinition,
    api_key: ApiKey | None,
    request_id: uuid.UUID,
    status_code: int,
    latency_ms: int,
    tokens_used: int,
    request_ip: str,
) -> None:
    """Write a UsageRecord for traffic monitoring. Non-fatal on error."""
    try:
        rec = UsageRecord(
            api_definition_id=api_def.id,
            api_key_id=api_key.id if api_key else None,
            api_code=api_def.api_code,
            request_id=request_id,
            status_code=status_code,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            request_ip=request_ip,
        )
        db.add(rec)
        db.commit()
    except Exception:
        db.rollback()
