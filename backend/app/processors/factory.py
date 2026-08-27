"""
ProcessorFactory — creates DocumentProcessor instances by type and model name.

Runtime selection format:  "processor_type|model_name"
Examples:
    "gemini|gemini-2.5-flash-preview-04-17"
    "openai|gpt-4o"
    "piaozone|gemini-2.5-flash-preview-04-17"
    "mock"
"""

from __future__ import annotations

import logging
from typing import List, Optional, Type

from app.processors.base import DocumentProcessor
from app.processors.mock_processor import MockProcessor

logger = logging.getLogger(__name__)

# ── optional imports ──────────────────────────────────────────────────────────

try:
    from app.processors.gemini_processor import GeminiProcessor
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False
    logger.warning("GeminiProcessor not available (missing google-genai)")

try:
    from app.processors.openai_processor import OpenAIDocumentProcessor
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
    logger.warning("OpenAIDocumentProcessor not available (missing openai)")

try:
    from app.processors.piaozone_processor import PiaoZoneProcessor
    _PIAOZONE_AVAILABLE = True
except ImportError:
    _PIAOZONE_AVAILABLE = False
    logger.warning("PiaoZoneProcessor not available (missing requests)")

try:
    from app.processors.qwen_processor import QwenProcessor
    _QWEN_AVAILABLE = True
except ImportError:
    _QWEN_AVAILABLE = False
    logger.warning("QwenProcessor not available (missing httpx/pymupdf)")


class ProcessorFactory:
    """Factory that creates and caches DocumentProcessor instances."""

    _registry: dict[str, Type[DocumentProcessor]] = {"mock": MockProcessor}

    def __init_subclass__(cls, **kwargs):  # noqa: ANN001
        super().__init_subclass__(**kwargs)

    @classmethod
    def _build_registry(cls) -> dict[str, Type[DocumentProcessor]]:
        reg: dict[str, Type[DocumentProcessor]] = {"mock": MockProcessor}
        if _GEMINI_AVAILABLE:
            reg["gemini"] = GeminiProcessor  # type: ignore[assignment]
        if _OPENAI_AVAILABLE:
            reg["openai"] = OpenAIDocumentProcessor  # type: ignore[assignment]
        if _PIAOZONE_AVAILABLE:
            reg["piaozone"] = PiaoZoneProcessor  # type: ignore[assignment]
        if _QWEN_AVAILABLE:
            reg["qwen"] = QwenProcessor  # type: ignore[assignment]
        return reg

    @classmethod
    def create(
        cls,
        processor_spec: str,
        *,
        model_name: Optional[str] = None,
        **kwargs,
    ) -> DocumentProcessor:
        """
        Create a processor from a spec string.

        Args:
            processor_spec: Either "processor_type" or "processor_type|model_name".
            model_name: Explicit model name (overrides the spec suffix).
            **kwargs: Extra keyword arguments forwarded to the processor constructor.

        Returns:
            A concrete DocumentProcessor instance.

        Raises:
            ValueError: If processor_type is not registered.
        """
        registry = cls._build_registry()

        # Parse "type|model" spec
        if "|" in processor_spec:
            proc_type, spec_model = processor_spec.split("|", 1)
        else:
            proc_type, spec_model = processor_spec, None

        proc_type = proc_type.strip().lower()
        resolved_model = model_name or spec_model

        if proc_type not in registry:
            raise ValueError(
                f"Unknown processor type: '{proc_type}'. "
                f"Available: {sorted(registry.keys())}"
            )

        processor_cls = registry[proc_type]

        if resolved_model:
            kwargs["model_name"] = resolved_model

        logger.info(
            "Creating processor: type=%s model=%s",
            proc_type,
            resolved_model or "(default)",
        )
        return processor_cls(**kwargs)

    @classmethod
    def create_from_settings(cls, **kwargs) -> DocumentProcessor:
        """
        Create a processor using DEFAULT_PROCESSOR from app settings.
        Falls back to MockProcessor if settings are unavailable.
        """
        try:
            from app.core.config import get_settings
            settings = get_settings()
            return cls.create(settings.DEFAULT_PROCESSOR, **kwargs)
        except Exception as exc:
            logger.warning("Failed to read DEFAULT_PROCESSOR from settings: %s. Using mock.", exc)
            return MockProcessor()

    @classmethod
    def available_types(cls) -> List[str]:
        """Return sorted list of available processor type names."""
        return sorted(cls._build_registry().keys())

    @classmethod
    def is_available(cls, processor_type: Optional[str]) -> bool:
        """A processor is available when its deps import AND its credentials exist.

        Registration (`_build_registry`) only proves the package imports; a
        provider without an API key still fails at call time (the exact failure
        behind「迭代优化进度始终为 0」— rows pinned to gemini on a server that
        can't reach it). mock / piaozone need no key here (piaozone resolves its
        token internally).
        """
        if not processor_type:
            return False
        proc = processor_type.strip().lower()
        if proc not in cls._build_registry():
            return False
        try:
            from app.core.config import get_settings
            s = get_settings()
        except Exception:  # pragma: no cover — settings unreadable → only mock is safe
            return proc == "mock"
        key_by_proc = {
            "gemini": s.gemini_api_keys,   # 单 key 或多 key 池，配了任意一个即可用
            "openai": s.OPENAI_API_KEY,
            "qwen": s.qwen_api_keys,       # 同上，单 key 或多 key 池
        }
        if proc in key_by_proc:
            return bool(key_by_proc[proc])
        return True

    @classmethod
    def resolve_spec(
        cls,
        preferred: Optional[str],
        model_name: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """Resolve (processor_type, model_name) honoring runtime availability.

        Order: `preferred`（per-API override，可用才生效）→ settings.DEFAULT_PROCESSOR
        → "mock"。降级时丢弃 model_name —— 旧 API 行里残留的 "gemini-2.5-flash"
        对 qwen 无意义（QwenProcessor 会忽略外族模型名，但其他处理器不会）。

        This is THE single entry point for every OCR / reflection / optimizer /
        extract call site. Per-row `processor_type` stays a preference, never a
        hard pin — switching DEFAULT_PROCESSOR in .env takes effect everywhere
        without DB migrations (same contract reprocess_document already had).
        """
        pref = (preferred or "").split("|", 1)[0].strip().lower() or None
        if pref and cls.is_available(pref):
            return pref, model_name

        # 行偏好不可用 → 交给主备策略（PRIMARY → BACKUP → DEFAULT_PROCESSOR
        # → mock）。resolve_chain 的链首就是「此刻该用哪个引擎」的答案，
        # 两者共用同一套判据，不会各说各话。
        head_proc, head_model = cls.resolve_chain(None, None)[0]
        if pref and head_proc != "mock":
            logger.warning(
                "Processor %r unavailable (missing key/deps) — falling back to "
                "%r; stale model_name %r dropped",
                pref, head_proc, model_name,
            )
        return head_proc, head_model

    @classmethod
    def is_saturated(cls, proc_type: Optional[str]) -> bool:
        """该 provider 的 key 池是否「严重满负荷」。未配置/无池 → 不算饱和
        （不可用是另一条判据，由 is_available 负责，别把两者混成一个信号）。"""
        proc = (proc_type or "").strip().lower()
        if proc in ("", "mock"):
            return False
        try:
            from app.core.config import get_settings
            from app.processors.key_pool import get_pool, keys_for

            if not keys_for(proc):
                return False
            limit = get_settings().PRIMARY_SATURATION_PER_KEY
            return get_pool(proc).is_saturated(limit)
        except Exception:  # noqa: BLE001 — 观测失败不该拖垮识别
            return False

    @classmethod
    def resolve_chain(
        cls,
        preferred: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> List[tuple[str, Optional[str]]]:
        """主备执行链：`[(proc, model), ...]`，按先后顺序尝试。

        策略（两台服务器同一份配置）：
          1. 行级 `preferred` 若可用 → 排第一（客户显式指定的引擎优先）；
          2. 否则 PRIMARY_PROCESSOR（默认 gemini）可用即排第一；
          3. BACKUP_PROCESSOR（默认 qwen）可用则**永远**附在后面做兜底；
          4. 主力**饱和**（每个账号都压满 / 全部熔断）时把备份提到前面 ——
             这就是「主力严重满负荷时用备份」；
          5. 都不可用 → `[("mock", None)]`（保持既有降级语义）。

        「主力不可用」在大陆机器上是**零成本**判定：没配 GEMINI_API_KEY →
        is_available 直接 False，不会发起任何一次注定超时的 Gemini 请求。

        降级到别的 provider 时丢弃 model_name —— 旧行里残留的
        "gemini-2.5-flash" 对 qwen 无意义。
        """
        try:
            from app.core.config import get_settings
            s = get_settings()
            primary = (s.PRIMARY_PROCESSOR or "").strip().lower()
            backup = (s.BACKUP_PROCESSOR or "").strip().lower()
        except Exception:  # pragma: no cover
            primary, backup = "", ""

        pref = (preferred or "").split("|", 1)[0].strip().lower() or None

        chain: List[tuple[str, Optional[str]]] = []
        seen: set[str] = set()

        def add(proc: Optional[str], model: Optional[str]) -> None:
            if proc and proc not in seen and cls.is_available(proc):
                chain.append((proc, model))
                seen.add(proc)

        add(pref, model_name)
        add(primary, model_name if primary == pref else None)
        add(backup, None)

        # 主力饱和 → 备份提到最前。只在「主力确实在链首」时重排，
        # 避免把客户显式指定的引擎也一并挪走。
        if len(chain) > 1 and cls.is_saturated(chain[0][0]):
            head = chain[0][0]
            chain = chain[1:] + [chain[0]]
            logger.warning(
                "主力 %s 严重满负荷（账号全忙或全部熔断），本次改走 %s",
                head, chain[0][0],
            )

        if not chain:
            # 保持与 resolve_spec 一致的最终兜底
            try:
                from app.core.config import get_settings
                default = (get_settings().DEFAULT_PROCESSOR or "").strip().lower()
            except Exception:  # pragma: no cover
                default = ""
            if default and cls.is_available(default):
                return [(default, None)]
            logger.warning(
                "主力 %r / 备份 %r / 偏好 %r 均不可用 —— 使用 mock",
                primary, backup, pref,
            )
            return [("mock", None)]
        return chain

    @classmethod
    def is_degraded_to_mock(cls, resolved_proc: Optional[str], preferred: Optional[str]) -> bool:
        """True 当 resolve_spec 落到 mock 且这不是有意配置——即行偏好或
        DEFAULT_PROCESSOR 本想要一个真实 provider，但都不可用被静默兜底。

        质量决策（评分聚合 / 早停 / 版本单调守护 / 判官）不得信任降级轮：
        mock 返回固定 fixture，对着它打分再「优化」prompt 是纯噪声迭代。
        有意配 mock（开发/测试：行偏好或 DEFAULT_PROCESSOR 显式 mock，或
        两者皆空）不算降级。
        """
        if (resolved_proc or "").strip().lower() != "mock":
            return False
        pref = (preferred or "").split("|", 1)[0].strip().lower()
        if pref == "mock":
            return False
        try:
            from app.core.config import get_settings
            default = (get_settings().DEFAULT_PROCESSOR or "").strip().lower()
        except Exception:  # pragma: no cover — settings 不可读时只剩 mock，视为有意
            default = "mock"
        if default == "mock" or not (pref or default):
            # DEFAULT_PROCESSOR 显式 mock（开发/测试）或完全无偏好 → 有意配置
            return False
        # pref/default 都想要真实 provider 但都不可用 → resolve_spec 静默兜底
        return True

    @classmethod
    def register(cls, name: str, processor_cls: Type[DocumentProcessor]) -> None:
        """Register a custom processor type at runtime."""
        if not (isinstance(processor_cls, type) and issubclass(processor_cls, DocumentProcessor)):
            raise TypeError("processor_cls must be a subclass of DocumentProcessor")
        cls._registry[name] = processor_cls
        logger.info("Registered custom processor: %s", name)
