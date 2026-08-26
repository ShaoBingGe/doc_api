"""提取结果缓存 —— 同一份文件不重复烧模型。

## 为什么单独建表而不是复用 async_tasks

同步端点不建任务行。若把缓存塞进 `async_tasks`，同步请求要么读不到、要么
得往任务列表里插"不是任务的行"，把对外的任务清单弄脏。缓存是缓存，
任务是任务，分开。

## 缓存键

    (client_id, template_id, content_hash)

- `content_hash`：文件字节的 sha256。**由服务端自己算** —— 实测对接方
  118 次提交里只有 8 次带了 fileHash（还都是内部测试），依赖调用方传是不行的。
- `template_id`：同一份文件在不同模板下抽取的字段完全不同。
- `client_id`：结果虽只由「文件 + 模板」决定、跨 client 复用在技术上安全，
  但仍按 client 隔离 —— 命中与否会泄露"别人是否传过同一份文件"，
  而收益仅限同一家重复提交，不值得拿这个换。

**刻意不把 prompt 版本进键**：TTL 只有 15 分钟，模板升级后最迟 15 分钟
缓存就自然失效，风险窗口足够小；换来的是键更简单、命中率更高。
若将来把 TTL 调长到小时级，必须重新把版本加进键 —— 否则模板升级
（如 2026-08 的字段清单修复）之后仍会吐旧结果，修复等于白做。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class ExtractionCache(TimestampMixin, Base):
    """一条命中即可跳过整次模型调用的结果。"""

    __tablename__ = "extraction_cache"

    #: sha256(client_id | template_id | content_hash)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        comment="文件字节的 sha256，服务端自算，不依赖调用方传 fileHash",
    )

    #: 同步端点的完整响应体 JSON 文本。traceId / sourceFileHash 在命中时会被
    #: 覆写成本次请求的值 —— 缓存的是识别结果，不是那一次请求的元数据。
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    doc_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    hits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="被复用次数，用于评估缓存收益",
    )
    last_hit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )


Index("ix_extraction_cache_lookup",
      ExtractionCache.client_id, ExtractionCache.content_hash)
