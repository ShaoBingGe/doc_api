"""异步任务终态结果的内存读缓存。

轮询是这套接口里唯一的高频操作（对接方拿到 taskId 后每隔几秒问一次），
而结果一旦进入终态就**永不改变**——正是缓存最擅长的形状。

设计取舍：**SQLite 是真相，这里只是挡在它前面的读缓存。**
反过来（内存为主、超时落盘）看似省一次写，实则做不到异步接口文档第 8 条要求的
「服务重启后未完成任务自动恢复」，而写入本来就只有 2 次/任务，不值得为此放弃重启安全。

有界性是硬要求：单条结果 5–50KB，默认上限 256 条 ≈ 最坏 12MB。
到顶淘汰最久未访问的（LRU），不会随任务量无限增长。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class TaskResultCache:
    """有界 LRU + TTL。线程安全（提取跑在线程池里，可能并发写入）。"""

    def __init__(self, max_size: int = 256, ttl_sec: int = 3600) -> None:
        self.max_size = max(1, max_size)
        self.ttl_sec = max(1, ttl_sec)
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if expires_at <= now:
                # 过期即删，不返回陈旧值
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)  # LRU：命中即最新
            self.hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time() + self.ttl_sec, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)  # 淘汰最久未访问

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._data),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }


_cache: TaskResultCache | None = None


def get_cache() -> TaskResultCache:
    global _cache
    if _cache is None:
        from app.core.config import get_settings

        s = get_settings()
        _cache = TaskResultCache(max_size=s.TASK_CACHE_SIZE, ttl_sec=s.TASK_CACHE_TTL_SEC)
    return _cache


def reset_cache() -> None:
    """仅供测试。"""
    global _cache
    _cache = None
