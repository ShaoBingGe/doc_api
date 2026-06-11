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
            "gemini": s.GEMINI_API_KEY,
            "openai": s.OPENAI_API_KEY,
            "qwen": s.QWEN_API_KEY,
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

        try:
            from app.core.config import get_settings
            default = (get_settings().DEFAULT_PROCESSOR or "").strip().lower()
        except Exception:  # pragma: no cover
            default = ""
        if default and default != pref and cls.is_available(default):
            if pref:
                logger.warning(
                    "Processor %r unavailable (missing key/deps) — falling back to "
                    "DEFAULT_PROCESSOR=%r; stale model_name %r dropped",
                    pref, default, model_name,
                )
            return default, None

        if pref or default:
            logger.warning(
                "Neither preferred=%r nor DEFAULT_PROCESSOR=%r is available — using mock",
                pref, default,
            )
        return "mock", None

    @classmethod
    def register(cls, name: str, processor_cls: Type[DocumentProcessor]) -> None:
        """Register a custom processor type at runtime."""
        if not (isinstance(processor_cls, type) and issubclass(processor_cls, DocumentProcessor)):
            raise TypeError("processor_cls must be a subclass of DocumentProcessor")
        cls._registry[name] = processor_cls
        logger.info("Registered custom processor: %s", name)
