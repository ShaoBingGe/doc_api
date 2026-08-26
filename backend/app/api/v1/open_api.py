"""开放平台端点（piaozone 兼容）。

路径与线上生产完全一致，**不挂在 /api/v1 前缀下**：

    POST /base/oauth/token
    POST /ai/knowledge/nlpService/document/analyze?access_token={token}
    POST /ai/knowledge/nlpService/overseaInvoice/extraction?access_token={token}
        └─ 别名，与上一条完全等价（存量对接方写死了这条路径）
    POST /ai/knowledge/nlpService/document/analyze/async?access_token={token}
    POST /ai/knowledge/nlpService/tasks/query?access_token={token}

同步与异步走**同一个准入闸**（services/extract_gate），所以"全服务并发不超过 N"
是一句真话，而不是两条路各自限流后相加。提取一律丢进线程池执行，绝不在
async 路由里直接调同步的 extract_document —— 那会独占事件循环整场识别。

与既有的 `/api/v1/extract/{api_code}`（X-API-Key）并存：前者给外部客户，
后者给工作区/前端。两条路径共用同一套提取管线（extract_service）。

契约要点（照抄线上，勿改）：
  * 业务响应 HTTP 恒为 200，成败看 `errcode`（"0000" 成功）；
  * 请求头 `client-platform: common`；Content-Type `multipart/form-data`；
  * 表单字段：templateId / fileHash / file / clientId；
  * `templateId` 是数字模板号，映射到 ApiDefinition.external_template_id。
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.deps import get_db
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.open_api_client import OpenApiClient
from app.services import async_task_service as tasksvc
from app.services import extract_service as svc
from app.services import open_api_auth as auth
from app.services import open_api_mapper as mapper
from app.services.async_task_worker import run_extraction
from app.services import extraction_cache as xcache
from app.services.extract_gate import GateTimeout, get_gate
from app.services.upload_validation import InvalidUpload, validate_upload

logger = logging.getLogger(__name__)
_settings = get_settings()

router = APIRouter(tags=["Open API (piaozone-compatible)"])


def _trace_id() -> str:
    """16 位小写十六进制，与线上 traceId 形态一致。"""
    return secrets.token_hex(8)


# ── 1. 取 access_token ────────────────────────────────────────────────────────

@router.post(
    "/base/oauth/token",
    summary="获取 access_token（开放平台）",
    description=(
        "sign = MD5(client_id + client_secret + timestamp)，小写十六进制。\n"
        "timestamp 为 unix 秒。成功返回 errcode=0000 与 access_token（36 小时有效）。"
    ),
)
def oauth_token(
    body: Annotated[dict, Body(...)],
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        payload = auth.issue_token(
            db,
            client_id=str(body.get("client_id") or ""),
            timestamp=str(body.get("timestamp") or ""),
            sign=str(body.get("sign") or ""),
        )
    except auth.OpenApiAuthError as exc:
        # 线上失败也走 200 + errcode，调用方不做 HTTP 分支
        return JSONResponse(
            status_code=200,
            content={"errcode": exc.errcode, "description": exc.description},
        )
    return JSONResponse(status_code=200, content=payload)


# ── 2. 文档解析 ───────────────────────────────────────────────────────────────

def _resolve_api_def(
    db: Session, *, template_id: str, client: OpenApiClient
) -> ApiDefinition:
    """templateId（数字）→ ApiDefinition，并校验该 client 有权访问。"""
    try:
        tid = int(str(template_id).strip())
    except (TypeError, ValueError):
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_NOT_FOUND, f"invalid templateId: {template_id!r}"
        ) from None

    api_def = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.external_template_id == tid)
        .first()
    )
    if api_def is None:
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_NOT_FOUND, f"templateId {tid} not found"
        )
    if api_def.status == ApiDefinitionStatus.deprecated:
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_FORBIDDEN, f"templateId {tid} has been deprecated"
        )

    # 租户隔离：模板挂了租户时，只有同租户的 client 能调用。
    # 平台桶模板（tenant_id 为空）对所有已鉴权 client 开放（公共国家模板）。
    if api_def.tenant_id is not None and api_def.tenant_id != client.tenant_id:
        raise auth.OpenApiAuthError(
            auth.ERR_TEMPLATE_FORBIDDEN,
            f"client {client.client_id} is not allowed to use templateId {tid}",
        )
    return api_def


def lookup_template(db: Session, template_id: str) -> ApiDefinition | None:
    """templateId → ApiDefinition，**不做权限校验**。

    给异步 worker 用：提交任务时已经校验过归属，执行时只需按号取模板。
    找不到或已停用返回 None（worker 据此把任务标 FAILED，而不是抛异常）。
    """
    try:
        tid = int(str(template_id).strip())
    except (TypeError, ValueError):
        return None
    api_def = (
        db.query(ApiDefinition)
        .filter(ApiDefinition.external_template_id == tid)
        .first()
    )
    if api_def is None or api_def.status == ApiDefinitionStatus.deprecated:
        return None
    return api_def


# 对外暴露的两条等价路径。第一条来自生产日志切片（规范路径）；第二条是
# 票易通「海外发票」业务线的历史路径 —— 对接方把它写死在了客户端代码里，
# 改不动，故服务端挂别名兜住。两条路径同一个 handler、同一套鉴权与提取管线，
# 行为逐字节一致；新接入方一律引导用规范路径。
ANALYZE_PATH = "/ai/knowledge/nlpService/document/analyze"
ANALYZE_PATH_ALIAS = "/ai/knowledge/nlpService/overseaInvoice/extraction"


@router.post(
    ANALYZE_PATH_ALIAS,
    summary="文档结构化解析（别名路径，等价于 document/analyze）",
    description=(
        f"与 `{ANALYZE_PATH}` 完全等价，供已写死此路径的存量对接方使用。\n"
        "新接入请用规范路径。"
    ),
)
@router.post(
    ANALYZE_PATH,
    summary="文档结构化解析（开放平台）",
    description=(
        "multipart/form-data：templateId / fileHash / file / clientId。\n"
        "HTTP 恒为 200，成败看 errcode（'0000' 成功）。"
    ),
)
async def analyze_document(
    request: Request,
    access_token: Annotated[str | None, Query()] = None,
    client_platform: Annotated[str | None, Header(alias="client-platform")] = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    trace_id = _trace_id()
    started = time.time()

    # 表单解析：字段名与线上一致，file 可缺省（由 errcode 兜底）
    template_id = file_hash = client_id = ""
    file_bytes: bytes | None = None
    filename: str | None = None
    try:
        form = await request.form()
        template_id = str(form.get("templateId") or "")
        file_hash = str(form.get("fileHash") or "")
        client_id = str(form.get("clientId") or "")
        upload = form.get("file")
        # 用 duck typing 而非 isinstance：starlette 的表单解析返回的是
        # starlette.datastructures.UploadFile，而 fastapi.UploadFile 是它的
        # **子类** —— isinstance(父类实例, 子类) 为 False，会把上传的文件误判成缺失。
        if upload is not None and hasattr(upload, "read"):
            file_bytes = await upload.read()
            filename = getattr(upload, "filename", None) or "upload"
    except Exception:  # noqa: BLE001 — 表单畸形不该 500
        logger.warning("analyze: malformed multipart body", exc_info=True)

    try:
        client = auth.resolve_token(db, access_token)
        # clientId 若同时出现在表单里，必须与 token 所属 client 一致，
        # 防止拿 A 的 token 冒充 B 提交
        if client_id and client_id != client.client_id:
            raise auth.OpenApiAuthError(
                auth.ERR_TEMPLATE_FORBIDDEN,
                "clientId does not match the authenticated access_token",
            )
        api_def = _resolve_api_def(db, template_id=template_id, client=client)
        if not file_bytes:
            raise auth.OpenApiAuthError(auth.ERR_NO_FILE, "file is required")
    except auth.OpenApiAuthError as exc:
        return JSONResponse(
            status_code=200,
            content=mapper.build_error(exc.errcode, exc.description, trace_id=trace_id),
        )

    # 受理期校验 + 取页数。与异步路径同一套判据：坏文件在占闸槽位、
    # 调模型之前就拦下（实测坏 jpg 会让模型回 400，白等一场）。
    # 同步契约不新增错误码，复用既有 5000，但描述说清哪儿不对。
    try:
        doc_pages = validate_upload(
            file_bytes, filename, max_bytes=_settings.max_upload_bytes)
    except InvalidUpload as exc:
        logger.info("analyze 拒收（文件不可用）: %s", exc.reason)
        return JSONResponse(
            status_code=200,
            content=mapper.build_error(
                auth.ERR_PROCESS_FAILED, exc.reason, trace_id=trace_id),
        )

    # 结果缓存：同一 client + 同一模板 + 同一份文件（服务端自算 sha256），
    # 15 分钟内直接复用上次结果，省掉整次模型调用。命中时 traceId 与
    # sourceFileHash 会被改写成本次请求的值。
    chash = xcache.content_hash(file_bytes)
    cached = xcache.lookup(
        db, client_id=client.client_id, template_id=template_id, chash=chash,
        trace_id=trace_id, source_file_hash=file_hash,
    )
    if cached is not None:
        payload, _ = cached
        db.close()
        return JSONResponse(status_code=200, content=payload)

    # 闸前先取出后面要用的标量并**归还数据库连接** —— 闸等待可达 120s，
    # 攥着连接等会耗干 QueuePool（默认 5+10），十几个并发 analyze 就能让
    # /base/oauth/token 排队到 30s 超时——正是本次改造要消灭的那个症状，
    # 只是换了个资源在堵（code review 抓出）。record_usage 阶段再开新会话。
    api_code = api_def.api_code
    api_def_id = api_def.id
    processor_type = api_def.processor_type
    client_id_for_cache = client.client_id
    db.close()

    # 复用既有提取管线（prompt 解析 / processor 兜底 / 审计一并沿用）。
    # 两处关键改动，见 services/extract_gate.py 与 async_task_worker.run_extraction：
    #   1) 过准入闸 —— 与异步 worker 共用同一个上限，"全服务并发 N"才是真话；
    #   2) 丢进线程池 —— extract_document 是同步函数，直接在 async 路由里调用会
    #      独占事件循环整场识别（实测 200s），期间取 token / 轮询 / 健康检查全部排队。
    try:
        async with get_gate().slot(doc_pages, timeout=_settings.SYNC_GATE_WAIT_SEC):
            result = await run_extraction(
                api_code=api_code,
                file_bytes=file_bytes,
                filename=filename,
                request_ip=request.client.host if request.client else "unknown",
            )
        # 取 entities（全量票据）；data 只有首条，多票据文档会漏
        structured: Any = result.entities if result.entities else result.data
    except GateTimeout as exc:
        # 满载：明确回"稍后重试"，不无限挂着（调用方有自己的 HTTP 超时）。
        # 复用既有 5000，不给已上线的同步契约新增错误码。
        logger.warning("analyze 拒绝（闸满）: %s", exc)
        return JSONResponse(
            status_code=200,
            content=mapper.build_error(
                auth.ERR_PROCESS_FAILED,
                "服务繁忙，请稍后重试（并发已达上限）",
                trace_id=trace_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 失败也走 errcode，不抛 500
        logger.exception("analyze failed: template=%s client=%s", template_id, client_id)
        return JSONResponse(
            status_code=200,
            content=mapper.build_error(
                auth.ERR_PROCESS_FAILED, f"analyze failed: {exc}", trace_id=trace_id
            ),
        )

    # doc_pages 报**原文档实际页数**（不是送模型的页数），调用方据此判断是否被截断。
    # 上面过闸时已经算过，这里直接复用。
    page_limit = _page_limit(processor_type)
    payload = mapper.build_response(
        structured,
        trace_id=trace_id,
        doc_pages=doc_pages,
        source_file_hash=file_hash,
        description=(
            mapper.TRUNCATED_DESC.format(limit=page_limit)
            if page_limit and doc_pages > page_limit
            else mapper.SUCCESS_DESC
        ),
    )
    if page_limit and doc_pages > page_limit:
        logger.info(
            "analyze: 文档 %d 页超过上限 %d，仅识别前 %d 页（template=%s trace=%s）",
            doc_pages, page_limit, page_limit, template_id, trace_id,
        )

    # 用量记录 + 结果入缓存（均非致命）。闸前已把请求会话归还连接池，
    # 这里开个短命新会话。
    try:
        db2 = SessionLocal()
        try:
            xcache.store(
                db2, client_id=client_id_for_cache, template_id=template_id,
                chash=chash, payload=payload, doc_pages=doc_pages,
            )
            api_def_row = db2.get(ApiDefinition, api_def_id)
            if api_def_row is not None:
                svc.record_usage(
                    db2,
                    api_def=api_def_row,
                    api_key=None,
                    request_id=uuid.uuid4(),
                    status_code=200,
                    latency_ms=int((time.time() - started) * 1000),
                    tokens_used=result.metadata.tokens_used,
                    request_ip=request.client.host if request.client else "unknown",
                )
        finally:
            db2.close()
    except Exception:  # noqa: BLE001
        logger.debug("record_usage failed (non-fatal)", exc_info=True)

    return JSONResponse(status_code=200, content=payload)


# ── 3. 异步识别：申请 + 查询 ──────────────────────────────────────────────────
#
# 契约见「异步文档处理 API 文档」。与同步端点的三点差别，改动前务必先看清：
#   1. 错误码用 A 系（A0301/A0410/A0426/A0700/C0110/1999），不是同步的 4xxx。
#      两套码是对接方文档写死的，不能统一 —— 响应里另附 legacyErrcode 供内部排查。
#   2. 申请接口的响应**没有 traceId、没有 docPages**，data 是对象不是数组。
#   3. 查询接口的 result 是**字符串**（同步响应的 JSON 文本），不是对象。

ANALYZE_ASYNC_PATH = "/ai/knowledge/nlpService/document/analyze/async"
TASKS_QUERY_PATH = "/ai/knowledge/nlpService/tasks/query"


@router.post(
    ANALYZE_ASYNC_PATH,
    summary="异步文档解析申请（开放平台）",
    description=(
        "multipart/form-data：file / templateId / fileHash / callbackUrl。\n"
        "立即返回 taskId，处理结果通过 tasks/query 轮询获取。\n"
        "HTTP 恒为 200，成败看 errcode（'0000' 成功）。"
    ),
)
async def analyze_document_async(
    request: Request,
    access_token: Annotated[str | None, Query()] = None,
    client_platform: Annotated[str | None, Header(alias="client-platform")] = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    template_id = file_hash = callback_url = ""
    file_bytes: bytes | None = None
    filename: str | None = None
    try:
        form = await request.form()
        template_id = str(form.get("templateId") or "0")
        file_hash = str(form.get("fileHash") or "")
        callback_url = str(form.get("callbackUrl") or "")
        upload = form.get("file")
        # duck typing 而非 isinstance —— 见同步端点里的同款说明
        if upload is not None and hasattr(upload, "read"):
            file_bytes = await upload.read()
            filename = getattr(upload, "filename", None) or "upload"
    except Exception:  # noqa: BLE001 — 表单畸形不该 500
        logger.warning("analyze/async: malformed multipart body", exc_info=True)
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_submit_error(
                tasksvc.ERR_UPLOAD_FAILED, "读取上传文件失败"
            ),
        )

    try:
        client = auth.resolve_token(db, access_token)
        if not file_bytes:
            raise tasksvc.AsyncTaskError(tasksvc.ERR_MISSING_PARAM, "file 不能为空")
        # 提交时就校验模板归属，别等到 worker 跑起来才发现越权
        _resolve_api_def(db, template_id=template_id, client=client)
    except auth.OpenApiAuthError as exc:
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_submit_error(
                tasksvc.ERR_UNAUTHORIZED, exc.description
            ),
        )
    except tasksvc.AsyncTaskError as exc:
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_submit_error(exc.errcode, exc.description),
        )

    try:
        task = tasksvc.create_task(
            db,
            client=client,
            template_id=template_id,
            file_bytes=file_bytes,
            filename=filename or "upload",
            file_hash=file_hash,
            callback_url=callback_url,
        )
    except tasksvc.AsyncTaskError as exc:
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_submit_error(exc.errcode, exc.description),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze/async 建任务失败: template=%s", template_id)
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_submit_error(tasksvc.ERR_FAIL, f"任务信息入库失败: {exc}"),
        )

    if callback_url:
        # 本期只入库不回调（已与对接方确认下一期做）。记一条日志，
        # 免得对方以为配了就会收到推送。
        logger.info(
            "任务 %s 提供了 callbackUrl 但本期不触发回调，请改用 tasks/query 轮询",
            task.id,
        )

    return JSONResponse(status_code=200, content=tasksvc.build_submit_ok(task.id))


@router.post(
    TASKS_QUERY_PATH,
    summary="批量查询异步任务状态（开放平台）",
    description=(
        'application/json：{"taskIds": ["…"]}，最多 10 个。\n'
        "返回以 taskId 为键的映射；result 为 JSON 字符串，需调用方自行解析。"
    ),
)
async def query_tasks(
    body: Annotated[dict, Body(...)],
    access_token: Annotated[str | None, Query()] = None,
    client_platform: Annotated[str | None, Header(alias="client-platform")] = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    trace_id = _trace_id()

    try:
        client = auth.resolve_token(db, access_token)
    except auth.OpenApiAuthError as exc:
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_query_error(
                tasksvc.ERR_UNAUTHORIZED, exc.description, trace_id=trace_id
            ),
        )

    raw = body.get("taskIds")
    task_ids = [str(t) for t in raw if str(t or "").strip()] if isinstance(raw, list) else []
    if not task_ids:
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_query_error(
                tasksvc.ERR_MISSING_PARAM, "taskIds 不能为空", trace_id=trace_id
            ),
        )
    if len(task_ids) > tasksvc.MAX_QUERY_TASK_IDS:
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_query_error(
                tasksvc.ERR_BATCH_TOO_LARGE,
                f"一次最多查询 {tasksvc.MAX_QUERY_TASK_IDS} 个任务，收到 {len(task_ids)} 个",
                trace_id=trace_id,
            ),
        )

    try:
        data = tasksvc.query_tasks(db, task_ids=task_ids, client_id=client.client_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("tasks/query 失败")
        return JSONResponse(
            status_code=200,
            content=tasksvc.build_query_error(
                tasksvc.ERR_FAIL, str(exc), trace_id=trace_id
            ),
        )

    # 结构化审计：记下这次轮询要了哪些 taskId、哪些**在本次被取走了终态结果**。
    # 访问日志只有 URL，taskId 在 JSON body 里 —— 没有这一行就无法回答
    # 「结果是什么时候被取走的」，只能按"完成后的第一次轮询"粗略推断。
    # 单行、字段定长、便于 grep 与脚本解析；taskId 取 8 位前缀（uuid4 前 8 位
    # 在本量级下碰撞可忽略），避免一行几百字符刷屏。
    logger.info(
        "tasks/query client=%s trace=%s n=%d delivered=[%s] pending=[%s] missing=%d",
        client.client_id,
        trace_id,
        len(task_ids),
        ",".join(f"{t[:8]}:{v.get('status')}"
                 for t, v in data.items()
                 if v.get("status") in ("COMPLETED", "FAILED")),
        ",".join(t[:8] for t, v in data.items() if v.get("status") == "PENDING"),
        len(task_ids) - len(data),
    )

    # 查不到的 taskId（不存在 / 属于别的 client）直接不出现在 map 里 ——
    # 不区分两者，避免泄露"这个 id 存在"这一信息。
    return JSONResponse(
        status_code=200,
        content={
            "errcode": tasksvc.ERR_OK,
            "description": "成功",
            "data": data,
            "traceId": trace_id,
            "legacyErrcode": tasksvc.LEGACY_OF[tasksvc.ERR_OK],
        },
    )


def _page_limit(processor_type: str | None) -> int | None:
    """该 API 实际生效的单次页数上限。

    取执行层的真实上限而非硬编码，避免 processor 改了上限、这里的提示语还停在旧值。
    解析不出（如 mock / gemini 无此限制）时返回 None，表示不提示截断。
    """
    from app.processors.factory import ProcessorFactory

    try:
        proc_used, _ = ProcessorFactory.resolve_spec(processor_type, None)
    except Exception:  # noqa: BLE001
        proc_used = processor_type
    if (proc_used or "").lower() == "qwen":
        from app.processors.qwen_processor import MAX_PAGES

        return MAX_PAGES
    return None


def _count_pages(file_bytes: bytes | None, filename: str | None) -> int:
    """docPages：PDF 数页数，图片恒为 1。解析失败回退 1，不影响主流程。"""
    if not file_bytes:
        return 0
    name = (filename or "").lower()
    if not name.endswith(".pdf"):
        return 1
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return len(doc)
    except Exception:  # noqa: BLE001
        return 1
