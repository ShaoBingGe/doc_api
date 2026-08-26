"""AsyncTask ORM —— 开放平台异步识别任务。

    POST ~/ai/knowledge/nlpService/document/analyze/async  → 建行(PENDING) + 返 taskId
    POST ~/ai/knowledge/nlpService/tasks/query             → 按 taskId 查状态/结果

## 为什么这张表是「真相」而不是内存

异步接口文档第 8 条要求「服务重启，未完成的任务会被自动恢复处理」——
结果放内存做不到这件事。写入只有 2 次/任务（建 + 终态），SQLite 微秒级，
本来就没有写吞吐问题；**高频的是轮询读**，那由 `task_result_cache` 挡在前面。

## 文件不在这张表里

上传的字节落到 `ASYNC_SPOOL_DIR` 下的独立文件，表里只存 `spool_path`。
所以一个排队中的任务只占约 200 字节内存 —— 这是"加队列不会撑爆内存"的关键。
进入终态后 spool 文件立即删除（结果已存下来，原件不再需要）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class TaskStatus:
    """任务状态。取值逐字对齐异步接口文档，**不要**改写成别的拼写。

    RUNNING 是内部态：对外查询时并入 PENDING —— 文档只定义了
    PENDING / COMPLETED / FAILED 三个对外值，多报一个会让对接方的
    状态机漏分支。
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"      # 内部态，对外映射为 PENDING
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    #: 对外可见的状态描述（文档 statusDesc 字段）
    DESC = {
        PENDING: "待处理",
        RUNNING: "待处理",
        COMPLETED: "已完成",
        FAILED: "处理失败",
    }

    #: 内部态 → 对外态
    PUBLIC = {
        PENDING: PENDING,
        RUNNING: PENDING,
        COMPLETED: COMPLETED,
        FAILED: FAILED,
    }

    TERMINAL = (COMPLETED, FAILED)


class AsyncTask(TimestampMixin, Base):
    """一次异步识别申请。"""

    __tablename__ = "async_tasks"

    # taskId 对外可见，用字符串 uuid4 而非 UUIDMixin：文档示例是带连字符的
    # 标准 uuid 文本，且它要作为 JSON map 的 key 原样回传。
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="taskId，对外返回并用作查询键",
    )

    # ── 归属（租户隔离用）────────────────────────────────────────────────
    client_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="提交该任务的开放平台 client_id；查询时只返回本 client 的任务",
    )
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, index=True, comment="冗余归属，便于按租户统计/清理",
    )

    # ── 请求参数（原样回显在 requestParams 里）────────────────────────────
    template_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="0", comment="对外数字模板号",
    )
    file_name: Mapped[str] = mapped_column(
        String(512), nullable=False, default="upload", comment="上传的原始文件名",
    )
    file_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="调用方给的 fileHash，回填到结果里",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", index=True,
        comment=(
            "文件字节的 sha256，**服务端自算**。实测对接方 118 次提交只有 8 次"
            "带了 fileHash，依赖调用方传是不行的；结果缓存按这个值去重。"
        ),
    )
    language: Mapped[str] = mapped_column(
        String(32), nullable=False, default="auto", comment="文档 requestParams.language",
    )
    callback_url: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="",
        comment="回调地址。**本期只入库不触发**，回调实现见下一期",
    )

    # ── 载荷 ─────────────────────────────────────────────────────────────
    spool_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, default="",
        comment="落盘的上传文件路径；进入终态后删除文件并置空",
    )
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="预估页数，准入闸按它扣页数配额（每页约占 30MB 内存）",
    )

    # ── 状态机 ───────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskStatus.PENDING, index=True,
    )
    result_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="同步接口完整响应的 JSON 文本。文档规定 result 是字符串，故原样存文本",
    )
    errcode: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="失败时的业务错误码（A 系）",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="失败原因，对外 errorMessage 字段",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="已重试次数，达上限后标 FAILED",
    )

    # ── 时间线 ───────────────────────────────────────────────────────────
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
        comment="过期时刻（默认建后 10 天），worker 定期清理",
    )


# 抢占任务的热查询：WHERE status=? ORDER BY created_at
Index("ix_async_tasks_status_created", AsyncTask.status, AsyncTask.created_at)
