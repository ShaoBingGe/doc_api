"""提取准入闸 —— 单体服务的唯一并发咽喉。

同步端点与异步 worker **共用同一个闸**，所以"全服务并发不超过 N"是一句真话，
而不是两条路各自限流后相加。

## 为什么是双维度

实测（qwen 渲染 DPI=150）：一页 PDF 渲染进内存约 **30MB**，与文件体积几乎无关
（0.5MB 的 6 页 PDF 让 RSS 从 48MB 涨到 235MB）。`qwen_processor.MAX_PAGES=16`，于是：

    3 个并发 × 1 页  ≈  90MB   ✅
    3 个并发 × 16 页 ≈ 1.4GB   ❌ 腾讯云 cgroup 限 900M，整机 1.9G

**只按文档数限流是不安全的** —— 同样是"并发 3"，可能占 90MB 也可能占 1.4GB。
所以放行需要同时满足两个条件：文档数未满 **且** 在途页数加上本次不超预算。

## 排队纪律：严格 FIFO（票号队列）

`asyncio.Condition` 只保证按 await 顺序**唤醒**，不保证按顺序**放行**——每个等待者
的准入谓词不同（页数不同），后到的小文档谓词先满足就会插队。没有票号队列时，
一个 16 页任务会被源源不断的小文档无限超车，异步任务 `slot()` 不带超时，
它就永远卡在 RUNNING：不重试、不失败、还占着 worker 的在途配额（真实缺陷，
2026-08 code review 抓出）。

所以放行加一条硬规则：**只有队头可以进**。代价是队头阻塞——大文档在队头时，
后面本来塞得下的小文档也要等；但闸内至多 max_docs 个在途、单次识别分钟级，
等待有界。饥饿是无界的，队头阻塞是有界的，取后者。

## 为什么用 asyncio 而不是 threading

闸要被两种调用方共用：HTTP 路由（协程）和后台 worker（协程）。用 asyncio 原语，
等待期间**不占线程**——线程只在真正开始提取时才从 anyio 线程池借出。若用
`threading.Semaphore`，每个排队请求都会占住一个线程，几十个排队就把线程池耗干。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class GateTimeout(Exception):
    """等待槽位超时 —— 调用方应回"服务繁忙，请稍后重试"，不要无限等。"""


class ExtractGate:
    """双维度准入闸：文档数 + 在途页数。

    单个文档的页数若本身就超过总预算（如 16 页文档 vs 12 页预算），仍然放行，
    但必须独占——否则该文档永远等不到槽位，成为静默黑洞。这是刻意的：
    宁可单个大文档把内存吃到上限，也不能让它永远不被处理。
    """

    def __init__(self, max_docs: int = 3, max_pages: int = 24) -> None:
        if max_docs < 1:
            raise ValueError("max_docs 至少为 1")
        if max_pages < 1:
            raise ValueError("max_pages 至少为 1")
        self.max_docs = max_docs
        self.max_pages = max_pages
        self._docs = 0
        self._pages = 0
        self._cond = asyncio.Condition()
        # FIFO 票号队列：只有 _queue[0] 有资格被放行（防大文档被小文档无限插队）
        self._queue: deque[int] = deque()
        self._next_ticket = 0

    # ── 观测 ──────────────────────────────────────────────────────────────
    @property
    def in_flight_docs(self) -> int:
        return self._docs

    @property
    def in_flight_pages(self) -> int:
        return self._pages

    def snapshot(self) -> dict:
        """给健康检查/日志用的瞬时快照。"""
        return {
            "docs": self._docs,
            "max_docs": self.max_docs,
            "pages": self._pages,
            "max_pages": self.max_pages,
            "queued": len(self._queue),
        }

    # ── 准入 ──────────────────────────────────────────────────────────────
    def _can_admit(self, pages: int) -> bool:
        if self._docs >= self.max_docs:
            return False
        if self._pages == 0:
            # 闸内空载：无论多大的文档都放行，否则超预算的大文档永远进不来。
            return True
        return self._pages + pages <= self.max_pages

    @asynccontextmanager
    async def slot(self, pages: int = 1, *, timeout: float | None = None):
        """占一个槽位；退出时无条件归还。

        pages 是**预估**页数（PDF 数页数、图片记 1）。估不准不影响正确性，
        只影响内存上界的精度，所以估不出来时按 1 记即可。
        """
        pages = max(1, int(pages or 1))
        await self._acquire(pages, timeout)
        try:
            yield
        finally:
            await self._release(pages)

    async def _acquire(self, pages: int, timeout: float | None) -> None:
        async def _wait() -> None:
            async with self._cond:
                ticket = self._next_ticket
                self._next_ticket += 1
                self._queue.append(ticket)
                try:
                    # 严格 FIFO：必须是队头**且**配额够才放行。谓词在每次
                    # notify 后重判，天然处理"惊群"。
                    await self._cond.wait_for(
                        lambda: self._queue[0] == ticket and self._can_admit(pages)
                    )
                except BaseException:
                    # 超时/取消：必须把票撤出队列并唤醒后继——队头的死票会
                    # 让整条队列永久卡死（比饥饿更糟）。
                    self._queue.remove(ticket)
                    self._cond.notify_all()
                    raise
                self._queue.popleft()
                self._docs += 1
                self._pages += pages
                # 队头易主，新队头可能同样进得来（如剩余页数还够小文档用）
                self._cond.notify_all()

        try:
            await asyncio.wait_for(_wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise GateTimeout(
                f"等待提取槽位超过 {timeout}s（在途 {self._docs}/{self.max_docs} 个文档、"
                f"{self._pages}/{self.max_pages} 页、排队 {len(self._queue)} 个）"
            ) from None

    async def _release(self, pages: int) -> None:
        async with self._cond:
            self._docs = max(0, self._docs - 1)
            self._pages = max(0, self._pages - pages)
            # notify_all 而非 notify：等待者的谓词各不相同（页数不同），
            # 只唤醒一个可能唤醒到一个恰好进不来的，导致其他能进的一起饿死。
            self._cond.notify_all()


_gate: ExtractGate | None = None


def get_gate() -> ExtractGate:
    """全进程唯一的闸。

    懒加载而非模块级实例化：`asyncio.Condition` 在 3.10+ 虽不再绑定创建时的事件循环，
    但延后到首次使用（此时一定在事件循环内）仍更稳妥，也让测试能通过 reset_gate()
    拿到干净状态。
    """
    global _gate
    if _gate is None:
        from app.core.config import get_settings

        s = get_settings()
        _gate = ExtractGate(max_docs=s.GATE_MAX_DOCS, max_pages=s.GATE_MAX_PAGES)
        logger.info(
            "提取准入闸就绪：最多 %d 个并发文档 / %d 页在途",
            _gate.max_docs, _gate.max_pages,
        )
    return _gate


def reset_gate() -> None:
    """仅供测试：丢弃当前闸，下次 get_gate() 重建。"""
    global _gate
    _gate = None
