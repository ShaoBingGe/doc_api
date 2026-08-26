"""异步识别任务：建任务 / 抢占 / 执行 / 查询 / 清理。

## A 系错误码

异步接口文档用的是 A0301/A0410/… 这套码，与已上线同步接口的 4xxx 是两套体系。
异步端点**主返 A 系**（对接方拿文档就能对），同时在响应里附 `legacyErrcode`
给内部排查用——两套码在同一个服务里并存是事实，不如把映射摆到明面上。
同步端点的 4xxx 一字不动（已上线契约）。

## 落盘时机

上传字节在**返回 taskId 之前**就写进 spool 目录，绝不在内存里等槽位。
这是"排队不吃内存"的关键：排队中的任务只占一行数据库记录（约 200 字节）。
进入终态后立即删 spool 文件——结果已存进 result_json，原件没有保留价值。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.async_task import AsyncTask, TaskStatus
from app.models.open_api_client import OpenApiClient
from app.services.upload_validation import InvalidUpload, validate_upload

logger = logging.getLogger(__name__)

# ── A 系错误码（异步接口文档）与既有 4xxx 的对照 ──────────────────────────────
ERR_OK = "0000"
ERR_UNAUTHORIZED = "A0301"        # 访问未授权
ERR_MISSING_PARAM = "A0410"       # 请求必填参数为空
ERR_BATCH_TOO_LARGE = "A0426"     # 请求批量处理总个数超出限制
ERR_UPLOAD_FAILED = "A0700"       # 用户上传文件异常
ERR_RPC_FAILED = "C0110"          # RPC 服务出错
ERR_FAIL = "1999"                 # 失败（未归类）

#: A 系 → 同步端点既有码，仅用于 legacyErrcode 字段（内部排查）
LEGACY_OF = {
    ERR_OK: "0000",
    ERR_UNAUTHORIZED: "4004",
    ERR_MISSING_PARAM: "4008",
    ERR_BATCH_TOO_LARGE: "",
    ERR_UPLOAD_FAILED: "",
    ERR_RPC_FAILED: "5000",
    ERR_FAIL: "5000",
}

MAX_QUERY_TASK_IDS = 10  # 文档：最多支持 10 个任务ID


class AsyncTaskError(Exception):
    """带 A 系 errcode 的业务失败。"""

    def __init__(self, errcode: str, description: str):
        super().__init__(description)
        self.errcode = errcode
        self.description = description


# ── 响应外壳 ─────────────────────────────────────────────────────────────────

def build_submit_ok(task_id: str) -> dict:
    """异步申请成功。**没有 traceId / docPages** —— 照文档，勿擅自补字段。"""
    return {
        "errcode": ERR_OK,
        "description": "成功",
        "data": {"taskId": task_id},
        "legacyErrcode": LEGACY_OF[ERR_OK],
    }


def build_submit_error(errcode: str, description: str) -> dict:
    return {
        "errcode": errcode,
        "description": description,
        "data": None,
        "legacyErrcode": LEGACY_OF.get(errcode, ""),
    }


def build_query_error(errcode: str, description: str, *, trace_id: str) -> dict:
    return {
        "errcode": errcode,
        "description": description,
        "data": {},
        "traceId": trace_id,
        "legacyErrcode": LEGACY_OF.get(errcode, ""),
    }


def task_to_public(task: AsyncTask) -> dict:
    """单个任务的对外视图（文档 data[taskId] 的结构）。

    RUNNING 在这里被折叠成 PENDING —— 文档只定义了三个对外状态，
    多报一个内部态会让对接方的状态机漏分支。
    """
    return {
        "taskId": task.id,
        "status": TaskStatus.PUBLIC.get(task.status, task.status),
        "statusDesc": TaskStatus.DESC.get(task.status, ""),
        "requestParams": {
            "templateId": task.template_id,
            "language": task.language,
            "fileName": task.file_name,
        },
        # 文档规定 result 是**字符串**（NLP 服务的原始 JSON，需调用方自行解析）
        "result": task.result_json if task.status == TaskStatus.COMPLETED else None,
        "errorMessage": task.error_message if task.status == TaskStatus.FAILED else None,
    }


# ── 建任务 ───────────────────────────────────────────────────────────────────

def _spool_dir() -> Path:
    d = Path(get_settings().ASYNC_SPOOL_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_task(
    db: Session,
    *,
    client: OpenApiClient,
    template_id: str,
    file_bytes: bytes,
    filename: str,
    file_hash: str = "",
    callback_url: str = "",
) -> AsyncTask:
    """落盘 + 建 PENDING 行。返回已 commit 的任务。

    准入检查全部在写盘**之前**：超限的提交不在磁盘上留任何字节。
    「计数 → 写盘 → 插行」之间没有 await，单进程事件循环下整段原子，
    并发提交不会双双越过配额检查。
    """
    s = get_settings()

    # 受理期校验：类型/大小/魔数/可解码性/尺寸。**在发 taskId 之前**拦下
    # 注定失败的文件——发出 taskId 是一个承诺，与其之后毁约（实测两张坏 jpg
    # 让对接方轮询几分钟、白烧 3 次模型重试），不如当场说清哪儿不对。
    # 校验顺带返回页数，省掉后面再解析一次。
    try:
        page_count = validate_upload(
            file_bytes, filename, max_bytes=s.max_upload_bytes)
    except InvalidUpload as exc:
        raise AsyncTaskError(ERR_UPLOAD_FAILED, exc.reason) from exc

    # 队列深度配额：全局挡服务被压垮，单 client 挡一家把队列占满饿死别家
    active = (TaskStatus.PENDING, TaskStatus.RUNNING)
    depth = db.query(AsyncTask).filter(AsyncTask.status.in_(active)).count()
    if depth >= s.ASYNC_MAX_QUEUE_DEPTH:
        raise AsyncTaskError(ERR_FAIL, "任务队列已满，请稍后重试")
    mine = (
        db.query(AsyncTask)
        .filter(AsyncTask.status.in_(active), AsyncTask.client_id == client.client_id)
        .count()
    )
    if mine >= s.ASYNC_MAX_QUEUE_PER_CLIENT:
        raise AsyncTaskError(
            ERR_FAIL,
            f"该 client 的排队任务已达上限（{s.ASYNC_MAX_QUEUE_PER_CLIENT}），"
            "请等待部分任务完成后再提交",
        )

    task_id = str(uuid.uuid4())

    # 先落盘再建行：反过来的话，建行成功而落盘失败会留下永远处理不了的任务
    spool = _spool_dir() / f"{task_id}{Path(filename or '').suffix or '.bin'}"
    try:
        spool.write_bytes(file_bytes)
    except OSError as exc:
        raise AsyncTaskError(ERR_UPLOAD_FAILED, f"写入上传文件失败: {exc}") from exc

    now = datetime.now(timezone.utc)
    task = AsyncTask(
        id=task_id,
        client_id=client.client_id,
        tenant_id=client.tenant_id,
        template_id=str(template_id or "0"),
        file_name=filename or "upload",
        file_hash=file_hash or "",
        callback_url=callback_url or "",
        spool_path=str(spool),
        page_count=max(1, page_count),
        status=TaskStatus.PENDING,
        expires_at=now + timedelta(days=s.ASYNC_TASK_TTL_DAYS),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info(
        "异步任务已入队 %s（client=%s template=%s %d 页 %.1fKB）",
        task_id, client.client_id, template_id, task.page_count, len(file_bytes) / 1024,
    )
    return task


# ── 抢占 / 状态流转 ──────────────────────────────────────────────────────────

def claim_next(db: Session) -> AsyncTask | None:
    """取一个 PENDING 任务并原子置为 RUNNING。抢不到返回 None。

    用带 status 条件的 UPDATE 而不是 "SELECT 然后 UPDATE"：即便将来回到多 worker
    部署，也不会两个进程处理同一个任务。单 worker 下这只是廉价的保险。
    """
    row = (
        db.query(AsyncTask)
        .filter(AsyncTask.status == TaskStatus.PENDING)
        .order_by(AsyncTask.created_at)
        .first()
    )
    if row is None:
        return None

    claimed = db.execute(
        update(AsyncTask)
        .where(AsyncTask.id == row.id, AsyncTask.status == TaskStatus.PENDING)
        .values(status=TaskStatus.RUNNING, started_at=datetime.now(timezone.utc))
    ).rowcount
    db.commit()
    if not claimed:
        return None  # 被别人抢先了
    db.refresh(row)
    return row


def _drop_spool(task: AsyncTask) -> None:
    """删除落盘文件。结果已入库，原件不再需要 —— 不删会把磁盘吃满。"""
    if not task.spool_path:
        return
    try:
        os.unlink(task.spool_path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("删除 spool 文件失败: %s", task.spool_path, exc_info=True)
    task.spool_path = ""


def _cache_terminal(task: AsyncTask) -> None:
    """把终态结果放进读缓存。

    连 `_client_id` 一起存：查询时要用它复核归属，缓存命中不能成为绕过
    租户隔离的后门。下划线前缀的键在返回给调用方前会被剥掉。
    """
    from app.services.task_result_cache import get_cache

    get_cache().put(task.id, {**task_to_public(task), "_client_id": task.client_id})


def mark_completed(db: Session, task: AsyncTask, payload: dict) -> None:
    """置 COMPLETED 并写入结果（序列化成字符串，文档要求 result 是字符串）。"""
    task.status = TaskStatus.COMPLETED
    task.result_json = json.dumps(payload, ensure_ascii=False)
    task.errcode = ERR_OK
    task.error_message = None
    task.finished_at = datetime.now(timezone.utc)
    _drop_spool(task)
    db.commit()
    _cache_terminal(task)


def mark_failed(db: Session, task: AsyncTask, errcode: str, message: str) -> None:
    """置 FAILED。**保留 spool 文件**以便事后取证。

    成功的任务删原件是对的（结果已入库，原件没有保留价值）；失败的反过来——
    2026-08-26 两张 jpg 被 Gemini 判 400，想回头看看到底什么问题时，
    文件已经被删干净了，只剩一句错误消息。失败量本就很少，且仍受
    `expires_at` 的 10 天清理约束，磁盘可控。
    """
    task.status = TaskStatus.FAILED
    task.errcode = errcode
    task.error_message = message
    task.finished_at = datetime.now(timezone.utc)
    db.commit()
    _cache_terminal(task)
    if task.spool_path:
        logger.warning(
            "异步任务 %s 失败，原件已保留待查：%s（%s: %s）",
            task.id, task.spool_path, errcode, message,
        )


def requeue_for_retry(db: Session, task: AsyncTask, reason: str) -> bool:
    """还有重试次数就退回 PENDING，返回是否已重新入队。

    退回时**不删 spool 文件** —— 重试还要读它。
    """
    s = get_settings()
    if task.retry_count >= s.ASYNC_MAX_RETRIES:
        return False
    task.retry_count += 1
    task.status = TaskStatus.PENDING
    task.started_at = None
    task.error_message = reason
    db.commit()
    logger.warning(
        "异步任务 %s 第 %d/%d 次重试：%s",
        task.id, task.retry_count, s.ASYNC_MAX_RETRIES, reason,
    )
    return True


def recover_orphans(db: Session) -> int:
    """启动时把残留的 RUNNING 退回 PENDING。→ 恢复条数。

    异步接口文档第 8 条：「如果服务重启，未完成的任务会被自动恢复处理」。
    单 worker 部署下，进程启动时不可能有真正在跑的 RUNNING，所以全部是上次
    非正常退出留下的孤儿，直接退回即可。
    """
    n = db.execute(
        update(AsyncTask)
        .where(AsyncTask.status == TaskStatus.RUNNING)
        .values(status=TaskStatus.PENDING, started_at=None)
    ).rowcount
    db.commit()
    if n:
        logger.info("启动恢复：%d 个中断的任务已退回待处理", n)
    return n


def purge_expired(db: Session) -> int:
    """删除过期任务行及其残留 spool 文件。→ 删除条数。"""
    now = datetime.now(timezone.utc)
    rows = db.query(AsyncTask).filter(AsyncTask.expires_at <= now).all()
    if not rows:
        return 0
    from app.services.task_result_cache import get_cache
    cache = get_cache()
    for r in rows:
        _drop_spool(r)
        cache.invalidate(r.id)
        db.delete(r)
    db.commit()
    logger.info("清理过期异步任务 %d 条", len(rows))
    return len(rows)


# ── 查询 ─────────────────────────────────────────────────────────────────────

def query_tasks(db: Session, *, task_ids: list[str], client_id: str) -> dict:
    """按 taskId 批量查询，返回 {taskId: 公开视图} 映射。

    **只返回属于本 client 的任务** —— 文档没提，但缺了它任何接入方都能凭猜到的
    taskId 读走别人的识别结果。他人的/不存在的 taskId 一律不出现在结果 map 里
    （不区分两者，避免泄露"这个 id 存在"这一信息，与现有越权返回 404 同源）。
    """
    from app.services.task_result_cache import get_cache

    cache = get_cache()
    out: dict[str, dict] = {}
    missing: list[str] = []

    for tid in task_ids:
        hit = cache.get(tid)
        # 缓存里也要验归属：不能因为缓存命中就跳过隔离检查
        if hit is not None and hit.get("_client_id") == client_id:
            out[tid] = {k: v for k, v in hit.items() if not k.startswith("_")}
        else:
            missing.append(tid)

    if missing:
        rows = (
            db.query(AsyncTask)
            .filter(AsyncTask.id.in_(missing), AsyncTask.client_id == client_id)
            .all()
        )
        for r in rows:
            out[r.id] = task_to_public(r)
            if r.status in TaskStatus.TERMINAL:
                _cache_terminal(r)

    return out
