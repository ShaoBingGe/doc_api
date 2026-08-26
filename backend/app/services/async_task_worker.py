"""异步任务后台 worker —— 单体服务里唯一的常驻协程。

## 为什么是 asyncio task 而不是线程

worker 要和同步 HTTP 路由**共用**同一个准入闸（`extract_gate`），闸是 asyncio 原语。
worker 若跑在线程里就得换一套跨线程原语，两条路的并发也就不再真正共用一个上限。
真正耗时的提取本身仍然会被丢进 anyio 线程池执行 —— 协程只负责排队和等待，不占线程。

## 与事件循环的关系

`run_extraction()` 把同步的 `extract_service.extract_document()` 包进
`anyio.to_thread.run_sync`。不这么做的话，一次 200 秒的识别会独占事件循环 200 秒，
期间取 token、结果轮询、健康检查全部排队——这正是接入前实测到的
「跑识别时取 token 30 秒超时」的成因。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import anyio

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.async_task import AsyncTask
from app.services import async_task_service as tasks
from app.services import extract_service as svc
from app.services import open_api_mapper as mapper
from app.services.extract_gate import get_gate

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
#: 已抢占、正在处理或正在闸前排队的任务。模块级而非 _loop 局部 ——
#: 停机时 stop_worker 要能把它们一并取消，否则 cancel 掉轮询协程后
#: 这些子任务会成为脱管的孤儿，继续跑到一半被进程杀掉、状态停在 RUNNING。
_in_flight: set[asyncio.Task] = set()
#: 每 N 轮空转做一次过期清理（N × 轮询间隔 ≈ 10 分钟）
_PURGE_EVERY_IDLE_TICKS = 300


async def run_extraction(
    *,
    api_code: str,
    file_bytes: bytes,
    filename: str,
    request_ip: str = "async-worker",
):
    """在线程池里跑一次提取，**不阻塞事件循环**。

    线程内新建 Session：SQLAlchemy Session 不是线程安全的，复用请求作用域的
    那个（`Depends(get_db)` 产出的）会在并发下产生难查的竞态。
    """

    def _work():
        db = SessionLocal()
        try:
            return svc.extract_document(
                db,
                api_code=api_code,
                api_key=None,
                file_bytes=file_bytes,
                filename=filename,
                request_ip=request_ip,
            )
        finally:
            db.close()

    # abandon_on_cancel=True：取消（停机/客户端断连）时立刻返回、放弃线程 ——
    # 线程里的模型调用无法被中断，任其跑到进程退出。默认的 False 会让
    # stop_worker 卡满整场识别（实测可达 200s），超过 systemd 优雅停机窗口后
    # 被 SIGKILL，结果同样丢失，还白等一场。
    return await anyio.to_thread.run_sync(_work, abandon_on_cancel=True)


def _finalize_completed(db, task: AsyncTask, api_def, structured, doc_pages: int) -> None:
    """把提取结果组装落库。**全程同步无 await** —— 这保证它在取消传播中
    也能完整执行（CancelledError 只在 await 点抛出）。"""
    from app.api.v1 import open_api as routes

    page_limit = routes._page_limit(api_def.processor_type)
    payload = mapper.build_response(
        structured,
        trace_id=task.id[:16].replace("-", ""),
        doc_pages=doc_pages,
        source_file_hash=task.file_hash,
        description=(
            mapper.TRUNCATED_DESC.format(limit=page_limit)
            if page_limit and doc_pages > page_limit
            else mapper.SUCCESS_DESC
        ),
    )
    tasks.mark_completed(db, db.merge(task), payload)


async def _process_one(task: AsyncTask) -> None:
    """处理单个已抢占（RUNNING）的任务。异常一律落到 FAILED 或重试，绝不外抛。"""
    db = SessionLocal()
    # (structured, doc_pages)：提取一旦完成就先存进它。取消若打在出闸的 await 上
    # （提取已结束、token 已计费），据此落库而不是丢弃重跑——丢弃等于同一份文档
    # 计费两次（code review 抓出的真实缺陷）。
    extraction: tuple | None = None
    try:
        spool = Path(task.spool_path)
        if not task.spool_path or not spool.exists():
            tasks.mark_failed(
                db, db.merge(task), tasks.ERR_UPLOAD_FAILED,
                f"落盘文件丢失: {task.spool_path or '(空)'}",
            )
            return

        from app.api.v1 import open_api as routes

        api_def = routes.lookup_template(db, task.template_id)
        if api_def is None:
            tasks.mark_failed(
                db, db.merge(task), tasks.ERR_UNAUTHORIZED,
                f"templateId {task.template_id} 不存在或已停用",
            )
            return

        # 闸在协程里 await —— 排队期间既不占线程也不占内存。
        # **文件必须在拿到槽位之后才读进内存**：反过来的话，N 个排队任务就是
        # N 份文件字节同时压在堆上，落盘队列的意义就没了。
        async with get_gate().slot(task.page_count):
            file_bytes = spool.read_bytes()
            result = await run_extraction(
                api_code=api_def.api_code,
                file_bytes=file_bytes,
                filename=task.file_name,
            )
            structured = result.entities if result.entities else result.data
            extraction = (structured, routes._count_pages(file_bytes, task.file_name))
            del file_bytes  # 出闸前就还回内存，别拖到函数结束

        _finalize_completed(db, task, api_def, *extraction)
        logger.info("异步任务 %s 完成（%d 页）", task.id, extraction[1])

    except asyncio.CancelledError:
        try:
            if extraction is not None:
                # 提取已完成、token 已计费——落库，绝不丢弃重跑
                _finalize_completed(db, task, api_def, *extraction)
                logger.info("停机：任务 %s 的已完成结果已落库", task.id)
            else:
                # 停机于提取中途：退回 PENDING，下次启动继续（不占用重试次数）
                fresh = db.merge(task)
                fresh.status = "PENDING"
                fresh.started_at = None
                db.commit()
        except Exception:  # noqa: BLE001
            logger.warning("停机时收尾任务 %s 失败", task.id, exc_info=True)
        raise
    except Exception as exc:  # noqa: BLE001 — worker 绝不能因单个任务而死
        logger.exception("异步任务 %s 处理失败", task.id)
        fresh = db.merge(task)
        if not tasks.requeue_for_retry(db, fresh, str(exc)):
            tasks.mark_failed(db, fresh, tasks.ERR_RPC_FAILED, str(exc))
    finally:
        db.close()


async def _loop() -> None:
    s = get_settings()
    gate = get_gate()
    idle_ticks = 0
    in_flight = _in_flight
    logger.info("异步任务 worker 已启动（轮询间隔 %.1fs）", s.ASYNC_POLL_INTERVAL_SEC)

    while True:
        # 只抢占「闸能装下」的量。无上限地抢会把一批任务标成 RUNNING 却干等在闸前——
        # 数据库状态就成了谎话（对外显示处理中，实际连槽位都没有），
        # 重启恢复时也分不清哪些真跑过。
        if len(in_flight) >= gate.max_docs:
            await asyncio.sleep(s.ASYNC_POLL_INTERVAL_SEC)
            continue

        db = SessionLocal()
        try:
            task = tasks.claim_next(db)
        except Exception:  # noqa: BLE001
            logger.exception("抢占任务失败，稍后重试")
            task = None
        finally:
            db.close()

        if task is None:
            idle_ticks += 1
            if idle_ticks >= _PURGE_EVERY_IDLE_TICKS:
                idle_ticks = 0
                db = SessionLocal()
                try:
                    tasks.purge_expired(db)
                    tasks.purge_stale_spools(db)
                    from app.services import extraction_cache as xcache
                    xcache.purge_expired(db)
                except Exception:  # noqa: BLE001
                    logger.exception("清理过期任务失败")
                finally:
                    db.close()
            await asyncio.sleep(s.ASYNC_POLL_INTERVAL_SEC)
            continue

        idle_ticks = 0
        # 不 await 处理本身：并发度由准入闸控制，这里放开让多个任务同时进闸。
        # 直接 await 会让 worker 退化成串行，闸的 max_docs>1 形同虚设。
        job = asyncio.create_task(_process_one(task))
        in_flight.add(job)
        job.add_done_callback(in_flight.discard)
        await asyncio.sleep(0)  # 让出一次，避免抢占循环跑满 CPU


async def start_worker() -> None:
    """由 lifespan 调用。幂等。"""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return

    db = SessionLocal()
    try:
        tasks.recover_orphans(db)   # 文档第 8 条：重启恢复未完成任务
        tasks.purge_expired(db)
        tasks.purge_stale_spools(db)
    except Exception:  # noqa: BLE001
        logger.exception("启动恢复失败（不阻塞服务启动）")
    finally:
        db.close()

    _worker_task = asyncio.create_task(_loop())


async def stop_worker() -> None:
    """由 lifespan 调用。先停抢占、再取消在途任务（各自退回 PENDING）。"""
    global _worker_task
    if _worker_task is None:
        return

    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    _worker_task = None

    # 在途任务各自的 CancelledError 分支会把自己退回 PENDING，
    # 下次启动继续处理且不消耗重试次数。
    if _in_flight:
        jobs = list(_in_flight)
        logger.info("停机：取消 %d 个在途任务并退回待处理", len(jobs))
        for j in jobs:
            j.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        _in_flight.clear()

    logger.info("异步任务 worker 已停止")
