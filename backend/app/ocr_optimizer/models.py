"""
SQLAlchemy ORM models for the OCR prompt optimizer subsystem.

Tables (see docs/ocr-optimizer-design.md §5):
  - ocr_prompt_versions     — a snapshot of the full modular prompt for an API
  - ocr_modules             — individual modules within a version
  - ocr_optimization_runs   — one manual `optimize()` invocation
  - ocr_optimization_rounds — one round within a run
  - ocr_module_iterations   — per-module per-round learning trail
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin


# ── Enums ─────────────────────────────────────────────────────────────────────

class PromptVersionStatus(str, Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class ModuleStatus(str, Enum):
    active = "active"
    frozen = "frozen"


class RunStatus(str, Enum):
    running = "running"
    paused_for_review = "paused_for_review"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"


class VersionOrigin(str, Enum):
    init = "init"
    round = "round"
    manual_edit = "manual_edit"


class SkillStatus(str, Enum):
    active = "active"
    archived = "archived"


class RoundPhase(str, Enum):
    ocr_running = "ocr_running"
    analyzing = "analyzing"
    optimizing = "optimizing"
    composing = "composing"
    completed = "completed"
    failed = "failed"


class CustomizeJobStatus(str, Enum):
    """Lifecycle of a customer customize job (see customer_iteration.py)."""
    queued = "queued"
    waiting_for_samples = "waiting_for_samples"
    reflecting = "reflecting"
    forking = "forking"
    optimizing = "optimizing"
    completed = "completed"
    failed = "failed"


# ── 1. ocr_prompt_versions ────────────────────────────────────────────────────

class OcrPromptVersion(UUIDMixin, Base):
    __tablename__ = "ocr_prompt_versions"
    __table_args__ = (
        UniqueConstraint("api_definition_id", "version", name="uq_ocr_prompt_versions_api_version"),
    )

    api_definition_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    # Version label string. Round products are integers like "1", "2"; manual_edit products
    # use "<parent>.<seq>" like "2.1", "2.2". Kept as String for flexibility.
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PromptVersionStatus.draft.value
    )
    # 'init' | 'round' | 'manual_edit' — where this version was produced. See design §5.2.
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VersionOrigin.init.value
    )
    composed_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    composed_schema: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    overall_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    produced_by_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    produced_in_round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    modules: Mapped[list["OcrModule"]] = relationship(
        "OcrModule",
        back_populates="prompt_version",
        cascade="all, delete-orphan",
        order_by="OcrModule.order_index",
    )


# ── 2. ocr_modules ────────────────────────────────────────────────────────────

class OcrModule(UUIDMixin, Base):
    __tablename__ = "ocr_modules"
    __table_args__ = (
        UniqueConstraint("prompt_version_id", "module_key", name="uq_ocr_modules_version_key"),
    )

    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ocr_prompt_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    json_path: Mapped[str] = mapped_column(String(256), nullable=False)
    schema_fragment: Mapped[dict] = mapped_column(JSON, nullable=False)
    ocr_suggestions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ocr_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # List of OcrSkill.id (uuid strings). MVP placeholder — composer doesn't read this yet
    # and optimizer is HARD-FORBIDDEN from modifying it (see design §15.10, §17).
    skill_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ModuleStatus.active.value
    )
    module_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    prompt_version: Mapped["OcrPromptVersion"] = relationship(
        "OcrPromptVersion", back_populates="modules"
    )


# ── 3. ocr_optimization_runs ──────────────────────────────────────────────────

class OcrOptimizationRun(UUIDMixin, Base):
    __tablename__ = "ocr_optimization_runs"

    api_definition_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    starting_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resulting_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RunStatus.running.value
    )
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    target_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.95)
    rounds_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Pointer to the last completed round_num. Next advance runs (current_round_num + 1).
    current_round_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_document_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    triggered_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    rounds: Mapped[list["OcrOptimizationRound"]] = relationship(
        "OcrOptimizationRound",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="OcrOptimizationRound.round_num",
    )


# ── 4. ocr_optimization_rounds ────────────────────────────────────────────────

class OcrOptimizationRound(UUIDMixin, Base):
    __tablename__ = "ocr_optimization_rounds"
    __table_args__ = (
        UniqueConstraint("run_id", "round_num", name="uq_ocr_optimization_rounds_run_num"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ocr_optimization_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_num: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    next_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    overall_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    per_sample_accuracy: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ocr_raw_outputs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    meta_decision: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    phase: Mapped[str] = mapped_column(
        String(24), nullable=False, default=RoundPhase.ocr_running.value
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["OcrOptimizationRun"] = relationship(
        "OcrOptimizationRun", back_populates="rounds"
    )
    iterations: Mapped[list["OcrModuleIteration"]] = relationship(
        "OcrModuleIteration",
        back_populates="round",
        cascade="all, delete-orphan",
    )


# ── 5. ocr_module_iterations ──────────────────────────────────────────────────

class OcrModuleIteration(UUIDMixin, Base):
    __tablename__ = "ocr_module_iterations"
    __table_args__ = (
        UniqueConstraint("round_id", "module_key", name="uq_ocr_module_iterations_round_module"),
    )

    round_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ocr_optimization_rounds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    per_sample_results: Mapped[list] = mapped_column(JSON, nullable=False)
    aggregate_accuracy: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    aggregate_diff: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    optimization_suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_ocr_suggestions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_ocr_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The ONLY field the optimizer is allowed to write regarding skills. See design §9, §15.10.
    skill_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_call_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    round: Mapped["OcrOptimizationRound"] = relationship(
        "OcrOptimizationRound", back_populates="iterations"
    )


# ── 6. customize_jobs (persistent state for the customer iteration pipeline) ──
#
# Replaces the in-memory job dict from MVP. A job tracks one "save my edits →
# fork + 3-round" lifecycle. Persistence enables:
#   - Crash recovery: surviving rows can be resumed or marked failed on boot.
#   - Sample gating: a job parked in `waiting_for_samples` lives in the DB
#     until the customer uploads enough samples, at which point we transition
#     it to `optimizing`.


class CustomizeJob(UUIDMixin, Base):
    __tablename__ = "customize_jobs"

    source_api_definition_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, index=True,
        comment="原始 ApiDefinition（客户在其上编辑字段）"
    )
    new_api_definition_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, index=True,
        comment="fork 出来的新 ApiDefinition（带客户专属 api_code）"
    )
    new_api_code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CustomizeJobStatus.queued.value
    )
    phase_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diffs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reflection_summary: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    rounds_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rounds_total: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    overall_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ── 7. ocr_skills (TODO — placeholder for §17) ────────────────────────────────

class OcrSkill(UUIDMixin, Base):
    """
    Reusable cross-module capability fragments (e.g. "how to read tables").

    **MVP status: TODO** — table exists for forward-compatible migration but
    no service currently reads or writes it. All API endpoints return
    501 Not Implemented. Optimizer is HARD-FORBIDDEN from modifying skills
    (see design §17.7 and §15.10).
    """
    __tablename__ = "ocr_skills"
    __table_args__ = (
        UniqueConstraint(
            "api_definition_id", "name", name="uq_ocr_skills_api_name"
        ),
    )

    # NULL = global library shared across all APIs; non-null = private to that API.
    api_definition_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Prompt fragment to inject when a module has this skill attached.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SkillStatus.active.value
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
