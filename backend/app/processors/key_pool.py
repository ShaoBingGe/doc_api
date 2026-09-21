"""多账号 API key 池 —— 并发均衡 + 配额熔断 + 饱和度观测。

Provider 无关：Gemini 和 Qwen 各持一个池实例（`get_pool("gemini")` /
`get_pool("qwen")`），加账号只改 `.env` 里对应的 `*_API_KEYS` 一行。

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

## 饱和度

`is_saturated()` 回答「这个 provider 现在还接得住吗」，供主备切换判定用
（见 factory.resolve_chain）：全部 key 在冷却 → 饱和；或**每个** key 的
在途都 ≥ 阈值 → 饱和。注意是 min 而非 sum：只要还有一个账号相对空闲，
就该继续用主力，而不是因为总量大就整体切备份。

## 线程安全

提取跑在 anyio 线程池里（多个 worker 线程并发调 process_document），
所有状态改动都在 `threading.Lock` 内。provider 的 client 对象按 key
缓存复用（建连有握手开销）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: 连续失败的冷却时长（秒）：30 → 60 → 120 → … 封顶 600
_COOLDOWN_BASE_SEC = 30.0
_COOLDOWN_MAX_SEC = 600.0

#: 只有这些特征的错误才算「账号级」故障（该冷却这个 key）；
#: 其余（坏文件 400、网络超时…）是请求级问题，不惩罚 key。
_QUOTA_MARKERS = (
    "429", "resource_exhausted", "quota", "rate limit", "rate_limit",
    "too many requests", "throttl",
)


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
    client: object = None        # provider client，惰性建、按 key 复用


class KeyPool:
    """一个 provider 的 key 池。所有公开方法线程安全。"""

    def __init__(self, keys: list[str], *, provider: str = "?"):
        deduped = list(dict.fromkeys(k.strip() for k in keys if k.strip()))
        if not deduped:
            raise ValueError(f"KeyPool({provider}) needs at least one non-empty key")
        self.provider = provider
        self._states = [_KeyState(key=k) for k in deduped]
        self._lock = threading.Lock()

    # ── 调度 ─────────────────────────────────────────────────────────────────

    def lease(self) -> "_Lease":
        """选一个 key 并占坑。用法：``with pool.lease() as lease:``
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
        # 全灭：选冷却到期最早的硬用。fail-open——上游有 retry/备份 provider，
        # 多挨一次 429 好过直接拒绝服务。
        state = min(self._states, key=lambda s: s.cooldown_until)
        logger.warning(
            "%s key 池全部在冷却中，fail-open 选用 #%d（%.0fs 后到期）",
            self.provider, self._states.index(state), state.cooldown_until - now,
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
                "%s key #%d 配额受限（连续 %d 次），冷却 %.0fs：%s",
                self.provider, self._states.index(state),
                state.consecutive_failures, backoff, str(exc)[:120],
            )

    # ── 健康 / 饱和 ──────────────────────────────────────────────────────────

    def healthy_count(self) -> int:
        now = time.monotonic()
        with self._lock:
            return sum(1 for s in self._states if s.cooldown_until <= now)

    def is_saturated(self, per_key_limit: int) -> bool:
        """「主力扛不住了」的判定，供主备切换用。

        两种情形算饱和：
          1. 全部 key 在冷却（配额打满/账号故障）；
          2. **每个**健康 key 的在途都 ≥ per_key_limit。

        用 min 而非 sum：只要还有一个账号相对空闲就该继续走主力——
        总量大不等于扛不住，2 个账号各 3 个在途和 1 个账号 6 个在途，
        对单账号 RPM 的压力完全不同。
        per_key_limit ≤ 0 表示关闭饱和切换（只在全冷却时才算饱和）。
        """
        now = time.monotonic()
        with self._lock:
            healthy = [s for s in self._states if s.cooldown_until <= now]
            if not healthy:
                return True
            if per_key_limit <= 0:
                return False
            return min(s.in_flight for s in healthy) >= per_key_limit

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
    """一次调用对一个 key 的占用。"""

    def __init__(self, pool: KeyPool, state: _KeyState):
        self._pool = pool
        self._state = state

    @property
    def key(self) -> str:
        return self._state.key

    def client(self, factory):
        """该 key 的 provider client（按 key 复用；双检锁惰性创建）。

        factory: 无参可调用，返回一个新 client（provider 各自提供）。
        """
        if self._state.client is None:
            with self._pool._lock:
                if self._state.client is None:
                    self._state.client = factory()
        return self._state.client

    def __enter__(self) -> "_Lease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._pool._report(self._state, exc)


# ── 进程级单例（按 provider）──────────────────────────────────────────────────

_pools: dict[str, KeyPool] = {}
_pools_lock = threading.Lock()


def keys_for(provider: str) -> list[str]:
    """该 provider 配置的 key 列表（`.env` 的 <PROVIDER>_API_KEYS 优先，
    回退单数版）。空列表 = 未配置。"""
    from app.core.config import get_settings

    s = get_settings()
    if provider == "gemini":
        keys = s.gemini_api_keys
        if not keys:
            import os
            if os.environ.get("API_KEY"):   # 兼容最早期的裸环境用法
                keys = [os.environ["API_KEY"]]
        return keys
    if provider == "qwen":
        return s.qwen_api_keys
    return []


def get_pool(provider: str) -> KeyPool:
    """该 provider 的池（首次调用时按 settings 建）。未配置 key 会 raise。"""
    pool = _pools.get(provider)
    if pool is None:
        with _pools_lock:
            pool = _pools.get(provider)
            if pool is None:
                pool = KeyPool(keys_for(provider), provider=provider)
                _pools[provider] = pool
                logger.info("%s key 池就绪：%d 个账号", provider, pool.size)
    return pool


def reset_pools() -> None:
    """测试用：丢弃全部单例，下次 get_pool 重读 settings。"""
    with _pools_lock:
        _pools.clear()
