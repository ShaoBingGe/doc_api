"""Gemini API key 池 —— 多账号并发均衡 + 故障熔断。

## 为什么是「最少在途」而不是轮询

识别耗时方差极大（6 页 ~10s，25 页 ~60s+）。纯轮询只保证**发起次数**均匀，
不保证**同一时刻的并发**均匀——慢任务会堆在某个账号上把它的 RPM 打满，
另一个账号却闲着。最少在途（least-in-flight）每次把新请求引到当前最空闲的
账号，两个维度都均匀：瞬时并发靠 in_flight 计数，长期累计靠平局时的
总次数轮转（total 最小者优先）。

## 熔断

某个 key 撞到 429 / RESOURCE_EXHAUSTED / 配额类错误 → 冷却（指数退避：
30s 起步、连续失败翻倍、封顶 10 分钟），期间调度器不选它，流量自动全部
落到健康 key。成功一次即清零退避。全部 key 都在冷却时选**冷却到期最早**
的硬用（fail-open）——拒绝服务比多挨一次 429 更糟，且上游还有 retry。

非配额类错误（400 坏文件、超时等）**不冷却**：那是这份文档或网络的问题，
不是这个账号的问题，冷却只会让好账号替坏文件背锅。

## 线程安全

提取跑在 anyio 线程池里（多个 worker 线程并发调 process_document），
所有状态改动都在 `threading.Lock` 内。`genai.Client` 按 key 缓存复用
（官方 SDK 的 Client 线程安全，且每次新建有握手开销）。

## 横向扩展

`.env` 配 `GEMINI_API_KEYS=key1;key2;key3`（分号或逗号分隔），加账号只改
这一行。不配则回退到单个 `GEMINI_API_KEY`，行为与无池时代逐字节一致。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: 连续失败的冷却时长（秒）：30 → 60 → 120 → … 封顶 600
_COOLDOWN_BASE_SEC = 30.0
_COOLDOWN_MAX_SEC = 600.0

#: 只有这些特征的错误才算「账号级」故障（该冷却这个 key）；
#: 其余（坏文件 400、网络超时…）是请求级问题，不惩罚 key。
_QUOTA_MARKERS = ("429", "resource_exhausted", "quota", "rate limit", "rate_limit")


def is_quota_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _QUOTA_MARKERS)


@dataclass
class _KeyState:
    key: str
    in_flight: int = 0          # 此刻用该 key 跑着的调用数
    total: int = 0              # 累计发起次数（平局轮转依据）
    consecutive_failures: int = 0
    cooldown_until: float = 0.0  # monotonic 时刻；0 = 健康
    client: object = None        # genai.Client，惰性建、按 key 复用


class GeminiKeyPool:
    """进程内单例（`get_pool()`）。所有公开方法线程安全。"""

    def __init__(self, keys: list[str]):
        deduped = list(dict.fromkeys(k.strip() for k in keys if k.strip()))
        if not deduped:
            raise ValueError("GeminiKeyPool needs at least one non-empty key")
        self._states = [_KeyState(key=k) for k in deduped]
        self._lock = threading.Lock()

    # ── 调度 ─────────────────────────────────────────────────────────────────

    def lease(self) -> "_Lease":
        """选一个 key 并占坑。用法：``with pool.lease() as (key, client_slot):``
        离开 with 块自动归还 in_flight（无论成败）。"""
        with self._lock:
            state = self._pick_locked()
            state.in_flight += 1
            state.total += 1
        return _Lease(self, state)

    def _pick_locked(self) -> _KeyState:
        now = time.monotonic()
        healthy = [s for s in self._states if s.cooldown_until <= now]
        if healthy:
            # 最少在途优先；平局取累计次数最少（等价于该并发档内轮转）
            return min(healthy, key=lambda s: (s.in_flight, s.total))
        # 全灭：选冷却到期最早的硬用。fail-open——上游有 retry，
        # 多挨一次 429 好过直接拒绝服务。
        state = min(self._states, key=lambda s: s.cooldown_until)
        logger.warning(
            "Gemini key 池全部在冷却中，fail-open 选用 #%d（%.0fs 后到期）",
            self._states.index(state), state.cooldown_until - now,
        )
        return state

    # ── 反馈 ─────────────────────────────────────────────────────────────────

    def _report(self, state: _KeyState, exc: BaseException | None) -> None:
        with self._lock:
            state.in_flight = max(0, state.in_flight - 1)
            if exc is None:
                state.consecutive_failures = 0
                state.cooldown_until = 0.0
                return
            if not is_quota_error(exc):
                return  # 请求级失败（坏文件/网络），不惩罚账号
            state.consecutive_failures += 1
            backoff = min(
                _COOLDOWN_BASE_SEC * (2 ** (state.consecutive_failures - 1)),
                _COOLDOWN_MAX_SEC,
            )
            state.cooldown_until = time.monotonic() + backoff
            logger.warning(
                "Gemini key #%d 配额受限（连续 %d 次），冷却 %.0fs：%s",
                self._states.index(state), state.consecutive_failures,
                backoff, str(exc)[:120],
            )

    # ── 观测 ─────────────────────────────────────────────────────────────────

    def stats(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "index": i,
                    "key_tail": s.key[-6:],
                    "in_flight": s.in_flight,
                    "total": s.total,
                    "cooling_sec": max(0.0, round(s.cooldown_until - now, 1)),
                }
                for i, s in enumerate(self._states)
            ]

    @property
    def size(self) -> int:
        return len(self._states)


class _Lease:
    """一次调用对一个 key 的占用。惰性建并缓存该 key 的 genai.Client。"""

    def __init__(self, pool: GeminiKeyPool, state: _KeyState):
        self._pool = pool
        self._state = state

    @property
    def key(self) -> str:
        return self._state.key

    def client(self):
        """该 key 的 genai.Client（按 key 复用；双检锁惰性创建）。"""
        if self._state.client is None:
            with self._pool._lock:
                if self._state.client is None:
                    from google import genai
                    self._state.client = genai.Client(api_key=self._state.key)
        return self._state.client

    def __enter__(self) -> "_Lease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._pool._report(self._state, exc)


# ── 进程级单例 ────────────────────────────────────────────────────────────────

_pool: GeminiKeyPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> GeminiKeyPool:
    """按当前 settings 建池（首次调用时）。keys 来源见 config.gemini_api_keys。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import os

                from app.core.config import get_settings
                keys = get_settings().gemini_api_keys
                if not keys and os.environ.get("API_KEY"):
                    # 兼容最早期的裸环境用法（settings 没配、只有环境变量）
                    keys = [os.environ["API_KEY"]]
                _pool = GeminiKeyPool(keys)
                logger.info("Gemini key 池就绪：%d 个账号", _pool.size)
    return _pool


def reset_pool() -> None:
    """测试用：丢弃单例，下次 get_pool 重读 settings。"""
    global _pool
    with _pool_lock:
        _pool = None
