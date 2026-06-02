"""
Offline prompt-evaluation harness (Prompt System v2 — Phase 0).

This package is the measurement substrate for every later prompt/skill
refactor. It runs a composed_prompt over a set of sample documents, scores
each module's extraction against ground truth using the SAME scoring path as
production (slicer.extract + evaluator.compare), and reports per-module +
overall accuracy. `benchmark_ab` runs two prompts over identical inputs and
reports the delta — the with/without comparison that skill-creator's loop is
built around.

Nothing here mutates the database or any version/round state: it is read-only
and side-effect-free, safe to run against live ApiDefs.

See docs/prompt-system-v2-plan.md (Phase 0).
"""

from .harness import (
    EvalReport,
    ModuleScore,
    ModuleSpec,
    benchmark_ab,
    collect_deviations,
    evaluate_prompt,
    module_specs_from_orm,
    score_outputs,
)

__all__ = [
    "EvalReport",
    "ModuleScore",
    "ModuleSpec",
    "benchmark_ab",
    "collect_deviations",
    "evaluate_prompt",
    "module_specs_from_orm",
    "score_outputs",
]
