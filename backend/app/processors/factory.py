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
    def register(cls, name: str, processor_cls: Type[DocumentProcessor]) -> None:
        """Register a custom processor type at runtime."""
        if not (isinstance(processor_cls, type) and issubclass(processor_cls, DocumentProcessor)):
            raise TypeError("processor_cls must be a subclass of DocumentProcessor")
        cls._registry[name] = processor_cls
        logger.info("Registered custom processor: %s", name)
