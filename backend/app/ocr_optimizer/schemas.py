"""Pydantic request/response schemas for ocr_optimizer endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── Module ────────────────────────────────────────────────────────────────────

class OcrModuleResponse(BaseModel):
    id: uuid.UUID
    module_key: str
    display_name: str
    description: str
    json_path: str
    schema_fragment: dict
    ocr_suggestions: dict
    ocr_prompt: str
    skill_ids: list[uuid.UUID] = []
    order_index: int
    status: str
    module_accuracy: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Prompt Version ────────────────────────────────────────────────────────────

class OcrPromptVersionSummary(BaseModel):
    id: uuid.UUID
    # String labels: "1", "2", "2.1" (manual_edit), "3"...
    version: str
    status: str
    origin: str = "init"
    overall_accuracy: float | None = None
    parent_version_id: uuid.UUID | None = None
    produced_by_run_id: uuid.UUID | None = None
    produced_in_round: int | None = None
    created_at: datetime
    activated_at: datetime | None = None
    module_count: int = 0
    composed_prompt_preview: str = ""

    model_config = ConfigDict(from_attributes=True)


class OcrPromptVersionDetail(BaseModel):
    id: uuid.UUID
    api_definition_id: uuid.UUID
    version: str
    status: str
    origin: str = "init"
    composed_prompt: str
    composed_schema: dict | None = None
    overall_accuracy: float | None = None
    parent_version_id: uuid.UUID | None = None
    produced_by_run_id: uuid.UUID | None = None
    produced_in_round: int | None = None
    created_at: datetime
    activated_at: datetime | None = None
    notes: str | None = None
    modules: list[OcrModuleResponse] = []

    model_config = ConfigDict(from_attributes=True)


# ── Init ──────────────────────────────────────────────────────────────────────

class InitVersionRequest(BaseModel):
    sample_document_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Sample documents bound to this API; persisted to ApiDefinition.config",
    )
    activate: bool = Field(
        default=True,
        description="Whether to mark the freshly-initialized version as active",
    )
    use_llm_for_modules: bool = Field(
        default=False,
        description="If True, call LLM to generate per-module description/prompt; "
                    "otherwise use heuristic fallbacks (faster, no LLM cost)",
    )


# ── Optimize ──────────────────────────────────────────────────────────────────

class OptimizeRequest(BaseModel):
    max_rounds: int | None = Field(default=None, ge=1, le=10)
    target_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_document_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Override the ApiDefinition's persisted samples for this run",
    )
    llm_provider: str | None = Field(
        default=None,
        description="processor_type|model spec for the optimizer LLM; defaults to API's processor",
    )


# ── Run / Round ───────────────────────────────────────────────────────────────

class IterationResponse(BaseModel):
    id: uuid.UUID
    module_key: str
    aggregate_accuracy: float
    aggregate_diff: dict | None = None
    optimization_suggestion: str | None = None
    new_description: str | None = None
    new_ocr_suggestions: dict | None = None
    new_ocr_prompt: str | None = None
    skill_feedback: str | None = None
    per_sample_results: list = []
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoundSummary(BaseModel):
    id: uuid.UUID
    round_num: int
    phase: str
    overall_accuracy: float | None = None
    prompt_version_id: uuid.UUID
    next_version_id: uuid.UUID | None = None
    meta_decision: dict | None = None
    duration_ms: int | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class RoundDetail(RoundSummary):
    per_sample_accuracy: dict | None = None
    ocr_raw_outputs: dict | None = None
    iterations: list[IterationResponse] = []


class RunSummary(BaseModel):
    id: uuid.UUID
    api_definition_id: uuid.UUID
    starting_version_id: uuid.UUID
    resulting_version_id: uuid.UUID | None = None
    status: str
    max_rounds: int
    target_accuracy: float
    rounds_completed: int
    current_round_num: int = 0
    sample_document_ids: list
    llm_provider: str
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    metrics: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class RunDetail(RunSummary):
    rounds: list[RoundSummary] = []


# ── Activate ──────────────────────────────────────────────────────────────────

class ActivateResponse(BaseModel):
    id: uuid.UUID
    version: str
    status: str
    activated_at: datetime | None = None


# ── Optimize trigger response ─────────────────────────────────────────────────

class OptimizeTriggerResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    rounds_completed: int
    current_round_num: int = 0
    starting_version_id: uuid.UUID
    resulting_version_id: uuid.UUID | None = None
    overall_accuracy: float | None = None
    error_message: str | None = None


# ── Advance / Finalize / Abort ────────────────────────────────────────────────

class AdvanceRequest(BaseModel):
    use_version_id: uuid.UUID | None = Field(
        default=None,
        description="Start the new round from this version. Typically a manual_edit "
                    "derived version (vX.Y). If omitted, uses the most recent round's "
                    "next_version_id.",
    )


class FinalizeRequest(BaseModel):
    version_id: uuid.UUID = Field(
        description="Version to activate. Must belong to this Run's evolution chain.",
    )


# ── Manual Patch ──────────────────────────────────────────────────────────────

class ModuleEdit(BaseModel):
    """One module's edits in a manual_patch request.

    NOTE: skill_ids / ocr_prompt are intentionally NOT in this schema.
    Optimizer cannot modify skills, and neither can users via this path
    (design §15.10). ocr_prompt is derived from description+suggestions.
    """
    model_config = ConfigDict(extra="forbid")
    module_key: str
    description: str | None = None
    ocr_suggestions: dict | None = None


class ManualPatchRequest(BaseModel):
    edits: list[ModuleEdit] = Field(default_factory=list)


# ── Skills (TODO placeholders) ────────────────────────────────────────────────

class OcrSkillResponse(BaseModel):
    id: uuid.UUID
    api_definition_id: uuid.UUID | None = None
    name: str
    description: str
    content: str
    status: str = "active"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OcrSkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    content: str
    api_definition_id: uuid.UUID | None = None
