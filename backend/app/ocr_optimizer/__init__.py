"""
OCR Prompt Optimization subsystem.

Replaces the legacy `app/optimizers/` single-string prompt optimizer with a
modular system where prompts are decomposed into independently-optimizable
modules. See docs/ocr-optimizer-design.md for the full design.

Public entry points:
  - `get_active_composed_prompt(db, api_def_id) -> str | None`
      Called by extract_service to fetch the active prompt for production OCR.
  - `start_optimization(db, api_def_id, ...) -> OcrOptimizationRun`
      Manual trigger for one optimization Run (Round 1, then paused_for_review).
  - `init_version(db, api_def_id, ...) -> dict`
      Auto-decompose response_schema into initial modules.
"""

# PEP 562 lazy re-exports. The public names below live in submodules
# (.models / .service.*) that themselves import `app.models`. Importing them
# EAGERLY here makes `python -m app.ocr_optimizer.eval.<cli>` crash on a
# cold-start circular import: running the CLI initializes THIS package first,
# its eager `from .models import ...` reaches into `app.models` which loops
# back into a half-initialized `app.ocr_optimizer.models`. Deferring the
# imports to first attribute access breaks the cycle — `app.models` (which
# already registers the OCR ORM tables on Base.metadata) becomes importable
# standalone, and the CLIs run via `-m` without a warmup shim.
import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, str] = {
    "OcrModule": ".models",
    "OcrModuleIteration": ".models",
    "OcrOptimizationRound": ".models",
    "OcrOptimizationRun": ".models",
    "OcrPromptVersion": ".models",
    "OcrSkill": ".models",
    "VersionOrigin": ".models",
    "abort_run": ".service.run_orchestrator",
    "advance_round": ".service.run_orchestrator",
    "finalize_run": ".service.run_orchestrator",
    "manual_patch": ".service.run_orchestrator",
    "start_optimization": ".service.run_orchestrator",
    "init_version": ".service.module_initializer",
    "get_active_composed_prompt": ".service.persistence",
}


def __getattr__(name: str) -> Any:  # PEP 562
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target, __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache so __getattr__ runs at most once per name
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


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
