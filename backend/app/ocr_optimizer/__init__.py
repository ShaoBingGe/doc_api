"""
OCR Prompt Optimization subsystem.

Replaces the legacy `app/optimizers/` single-string prompt optimizer with a
modular system where prompts are decomposed into independently-optimizable
modules. See docs/ocr-optimizer-design.md for the full design.

Public entry points:
  - `get_active_composed_prompt(db, api_def_id) -> str | None`
      Called by extract_service to fetch the active prompt for production OCR.
  - `optimize(db, api_def_id, ...) -> dict`
      Manual trigger for one optimization Run.
  - `init_version(db, api_def_id, ...) -> dict`
      Auto-decompose response_schema into initial modules.
"""

from .models import (
    OcrModule,
    OcrModuleIteration,
    OcrOptimizationRound,
    OcrOptimizationRun,
    OcrPromptVersion,
    OcrSkill,
    VersionOrigin,
)
from .service.run_orchestrator import (
    abort_run,
    advance_round,
    finalize_run,
    manual_patch,
    start_optimization,
)
from .service.module_initializer import init_version
from .service.persistence import get_active_composed_prompt

__all__ = [
    "OcrPromptVersion",
    "OcrModule",
    "OcrOptimizationRun",
    "OcrOptimizationRound",
    "OcrModuleIteration",
    "OcrSkill",
    "VersionOrigin",
    "start_optimization",
    "advance_round",
    "finalize_run",
    "abort_run",
    "manual_patch",
    "init_version",
    "get_active_composed_prompt",
]
