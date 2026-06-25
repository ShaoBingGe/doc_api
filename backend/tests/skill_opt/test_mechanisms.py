"""L1 — ReflACT mechanism behavior (gates P1). ALL token-free: every case
injects synthetic rollout scores, never the real VLM.

Catalog: docs/skill-optimization-test-cases.md §2 L1. Docstrings = specs;
bodies skip until P1 mechanisms land.
"""
import pytest

pytestmark = pytest.mark.skip(reason="P1 pending: ReflACT mechanisms")


# ── Held-out validation Gate (soft + rolling leave-one-out) ──────────────────

def test_SKT_G01_gate_rejects_overfit(synthetic_rollouts):
    """Candidate UP on train but DOWN on held-out val → Gate REJECTS, version
    rolls back to current. (The central anti-overfitting guarantee.)"""


def test_SKT_G02_gate_accepts_real_gain(synthetic_rollouts):
    """Candidate strictly UP on val → Gate ACCEPTS."""


def test_SKT_G03_gate_rejects_flat_or_down(synthetic_rollouts):
    """val equal or lower → REJECT (strictly-greater acceptance)."""


def test_SKT_G04_rolling_leave_one_out(synthetic_rollouts):
    """12 samples → LOO folds: each fold's val non-empty, the 3 anchors always
    stay in train, every sample serves as val exactly the right number of times."""


def test_SKT_G05_soft_metric_discriminates(synthetic_rollouts):
    """A 1-field partial improvement (hard unchanged) → soft gate detects it,
    hard gate cannot. Justifies the soft default for few samples."""


# ── Edit budget / Clip / autonomous LR ───────────────────────────────────────

def test_SKT_C01_clip_top_L(fake_edits=None):
    """8 candidate edits, clip(L=3) → exactly 3, ordered by support_count desc."""


def test_SKT_C02_typed_bounded_edit_no_full_rewrite():
    """optimize_module emits typed FieldEdit (append/replace/delete); resulting
    prompt diff line-count ≤ threshold (no whole-prompt rewrite)."""


def test_SKT_C03_autonomous_lr_scales_with_severity(synthetic_rollouts):
    """Severe under-target (acc 0.2) → larger L; mild (0.9) → smaller L."""


# ── Minibatch reflect + aggregate + support ──────────────────────────────────

def test_SKT_M01_minibatch_grouped_reflection():
    """5 same-field diffs reflected as ONE grouped call (not 5)."""


def test_SKT_M02_support_count(synthetic_rollouts):
    """3 samples share an error + 1 unique error → shared edit support_count=3,
    unique=1."""


def test_SKT_M03_aggregate_dedup():
    """Two samples produce synonymous edits → merged to one, support summed."""


# ── SKILL_DEFECT vs EXECUTION_LAPSE ──────────────────────────────────────────

def test_SKT_D01_systematic_error_is_defect(synthetic_rollouts):
    """k/N samples share the error → SKILL_DEFECT → body edit."""


def test_SKT_D02_oneoff_is_lapse(synthetic_rollouts):
    """1/N one-off slip → EXECUTION_LAPSE → appendix only, body bytes UNCHANGED."""


def test_SKT_D03_unsure_defaults_to_lapse():
    """Ambiguous signal → default EXECUTION_LAPSE (protect the body)."""


# ── Rejected-edit buffer ─────────────────────────────────────────────────────

def test_SKT_R01_rejected_not_reproposed(synthetic_rollouts):
    """Edit X rejected round 1 → round 2 reflect/clip candidates exclude X."""


def test_SKT_R02_buffer_persists_across_rounds():
    """Buffer accumulates and is read/written from run state across rounds."""


# ── Noise-sample gate (pre-iteration) ────────────────────────────────────────

def test_SKT_N01_noise_sample_threshold():
    """Confirmed < 3+N (=12) → iteration blocked + prompt to upload more;
    ≥ 12 → allowed."""
