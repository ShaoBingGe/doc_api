"""L1 — ReflACT mechanism behavior (gates P1). ALL token-free: every case uses
injected synthetic rollout scores / typed edits, never the real VLM.

Catalog: docs/skill-optimization-test-cases.md §2 L1. Pure-unit cases are live;
two integration cases (C02 typed-edit-in-optimize_module, M01 minibatch LLM
grouping) skip until P1 wires the modules into run_orchestrator.
"""
import pytest

from app.ocr_optimizer.skilltrain import aggregate, buffer, classify, clip, gate, noise_gate
from app.ocr_optimizer.skilltrain.types import (
    EXECUTION_LAPSE,
    SKILL_DEFECT,
    FieldEdit,
)


# ── Held-out validation Gate (soft + rolling leave-one-out) ──────────────────

def test_SKT_G01_gate_rejects_overfit(synthetic_rollouts):
    """Candidate UP on train but DOWN on held-out val → Gate REJECTS."""
    cur_val = gate.score(synthetic_rollouts({"v1": {"f": (True, 0.9)}}))
    cand_val = gate.score(synthetic_rollouts({"v1": {"f": (False, 0.4)}}))  # worse on val
    res = gate.decide(cur_val, cand_val)
    assert not res.accepted and res.action.value == "reject"


def test_SKT_G02_gate_accepts_real_gain(synthetic_rollouts):
    """Candidate strictly UP on val → Gate ACCEPTS."""
    cur = gate.score(synthetic_rollouts({"v1": {"f": (False, 0.5)}}))
    cand = gate.score(synthetic_rollouts({"v1": {"f": (True, 0.9)}}))
    assert gate.decide(cur, cand).accepted


def test_SKT_G03_gate_rejects_flat_or_down(synthetic_rollouts):
    """val equal or lower → REJECT (strictly-greater acceptance)."""
    assert not gate.decide(0.7, 0.7).accepted     # flat
    assert not gate.decide(0.7, 0.6).accepted     # down


def test_SKT_G04_rolling_leave_one_out():
    """12 samples, 3 anchors → folds: anchors always in train, each non-anchor
    serves as val exactly once."""
    ids = [f"s{i}" for i in range(12)]
    anchors = ["s0", "s1", "s2"]
    folds = gate.rolling_leave_one_out(ids, anchors=anchors)
    assert len(folds) == 9                                   # 12 - 3 anchors
    val_seen = [v[0] for _t, v in folds]
    assert sorted(val_seen) == sorted(ids[3:])              # each non-anchor val once
    for train, val in folds:
        assert set(anchors).issubset(train)                # anchors never held out
        assert val[0] not in train


def test_SKT_G05_soft_metric_discriminates(synthetic_rollouts):
    """A 1-field partial improvement (hard unchanged) → soft gate detects it,
    hard gate cannot. Justifies the soft default."""
    cur = synthetic_rollouts({"v1": {"a": (True, 1.0), "b": (False, 0.30)}})
    cand = synthetic_rollouts({"v1": {"a": (True, 1.0), "b": (False, 0.70)}})  # b improved, still hard-wrong
    assert gate.decide(gate.score(cur, "soft"), gate.score(cand, "soft")).accepted
    assert not gate.decide(gate.score(cur, "hard"), gate.score(cand, "hard")).accepted


# ── Edit budget / Clip / autonomous LR ───────────────────────────────────────

def test_SKT_C01_clip_top_L():
    """8 candidate edits, clip(L=3) → exactly 3, ordered by support_count desc."""
    edits = [FieldEdit(op="append", target=f"f{i}", content=f"c{i}", support_count=i) for i in range(8)]
    top = clip.rank_and_select(edits, 3)
    assert len(top) == 3
    assert [e.support_count for e in top] == [7, 6, 5]


def test_SKT_C03_autonomous_lr_scales_with_severity():
    """Severe under-target (acc 0.2 → severity 0.8) → larger L than mild (0.9 → 0.1)."""
    severe = clip.decide_L(1 - 0.2)
    mild = clip.decide_L(1 - 0.9)
    assert severe > mild
    assert mild >= 1  # never zero edits for a non-perfect field


# ── Minibatch aggregate + support ────────────────────────────────────────────

def test_SKT_M02_support_count():
    """3 samples share an edit + 1 unique → shared support_count=3, unique=1."""
    edits = [
        FieldEdit(op="append", target="invoiceNumber", content="strip T-prefix"),
        FieldEdit(op="append", target="invoiceNumber", content="strip T-prefix"),
        FieldEdit(op="append", target="invoiceNumber", content="strip T-prefix"),
        FieldEdit(op="append", target="currency", content="default JPY"),
    ]
    merged = {e.target: e.support_count for e in aggregate.aggregate_edits(edits)}
    assert merged["invoiceNumber"] == 3
    assert merged["currency"] == 1


def test_SKT_M03_aggregate_dedup():
    """Synonymous edits (same content modulo whitespace/case) → merged to one."""
    edits = [
        FieldEdit(op="replace", target="currency", content="Default  JPY"),
        FieldEdit(op="replace", target="currency", content="default jpy"),
    ]
    merged = aggregate.aggregate_edits(edits)
    assert len(merged) == 1
    assert merged[0].support_count == 2


# ── SKILL_DEFECT vs EXECUTION_LAPSE ──────────────────────────────────────────

def test_SKT_D01_systematic_error_is_defect():
    """3/5 samples share the error → SKILL_DEFECT."""
    assert classify.classify(error_count=3, n_samples=5) == SKILL_DEFECT


def test_SKT_D02_oneoff_is_lapse():
    """1/5 one-off slip → EXECUTION_LAPSE."""
    assert classify.classify(error_count=1, n_samples=5) == EXECUTION_LAPSE


def test_SKT_D03_unsure_defaults_to_lapse():
    """Sub-threshold / ambiguous (2/6 ≈ 0.33 < 0.34) → default EXECUTION_LAPSE
    (protect the body)."""
    assert classify.classify(error_count=2, n_samples=6) == EXECUTION_LAPSE
    assert classify.classify(error_count=0, n_samples=0) == EXECUTION_LAPSE


# ── Rejected-edit buffer ─────────────────────────────────────────────────────

def test_SKT_R01_rejected_not_reproposed():
    """Edit X rejected → candidates filtered to exclude X (by signature)."""
    x = FieldEdit(op="append", target="invoiceDate", content="use 和暦→西暦")
    y = FieldEdit(op="append", target="totalAmount", content="strip 円")
    buf = buffer.RejectedEditBuffer()
    buf.add(x)
    assert buf.contains(x)
    assert [e.target for e in buf.filter([x, y])] == ["totalAmount"]


def test_SKT_R02_buffer_persists_across_rounds():
    """Buffer round-trips through a plain list (run.metrics persistence)."""
    x = FieldEdit(op="replace", target="currency", content="JPY default")
    buf = buffer.RejectedEditBuffer()
    buf.add(x)
    restored = buffer.RejectedEditBuffer.from_list(buf.to_list())
    assert restored.contains(x)
    assert len(restored) == 1


# ── Noise-sample gate (pre-iteration) ────────────────────────────────────────

def test_SKT_N01_noise_sample_threshold():
    """Confirmed < 12 (3+9) → not ready + shortfall; ≥ 12 → ready."""
    assert noise_gate.required_total() == 12
    assert not noise_gate.is_ready(11)
    assert noise_gate.shortfall(11) == 1
    assert noise_gate.is_ready(12)
    assert noise_gate.shortfall(12) == 0


# SKT_C02 (typed bounded edit, no full rewrite) and SKT_M01 (minibatch grouped
# reflection) are now implemented at the loop level in test_driver.py.
