"""
Abstract base class and shared types for document processors.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProcessingResult(BaseModel):
    """Unified output schema for all document processors."""

    structured_data: dict = Field(
        default_factory=dict,
        description="Extracted structured fields from the document",
    )
    inferred_schema: dict | None = Field(
        default=None,
        description="JSON Schema inferred from the extracted data (optional)",
    )
    raw_text: str | None = Field(
        default=None,
        description="Raw text returned by the model before JSON parsing",
    )


class DocumentProcessor(ABC):
    """Abstract base class for document processing strategies."""

    @abstractmethod
    def process_document(
        self,
        file_path: str,
        instruction: str,
        runtime_config: Optional[dict] = None,
    ) -> str:
        """Process a document and return extracted information as JSON string."""

    @abstractmethod
    def get_model_version(self) -> str:
        """Return the model version identifier (format: processor_type|model_name)."""


# ── Utility helpers ────────────────────────────────────────────────────────────

def extract_json(text: str) -> list[str]:
    """Extract JSON blocks embedded between ```json ... ``` fences."""
    pattern = r"```json(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches]
