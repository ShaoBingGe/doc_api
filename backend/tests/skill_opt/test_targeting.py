"""L1 — disciplined optimize-target selection (defect-filter + clip). Token-free.

Backs the SKILL_EDIT_DISCIPLINE production wiring (which modules a round optimizes).
"""
from types import SimpleNamespace

from app.ocr_optimizer.skilltrain import targeting


def _it(key, accs):
    """An iteration-like with per-sample field accuracies (0/1 per sample)."""
    return SimpleNamespace(
        module_key=key,
        aggregate_accuracy=sum(accs) / len(accs),
        per_sample_results=[
            {"sample_doc_id": f"s{i}", "field_accuracy": float(a)} for i, a in enumerate(accs)
        ],
    )


def test_targeting_skips_one_off_lapse():
    """A field wrong on just 1/8 samples is a one-off lapse → NOT optimized; a
    field wrong on 5/8 is a systematic defect → optimized."""
    its = [
        _it("invoice_date", [1, 1, 1, 1, 1, 1, 1, 0]),      # 1/8 wrong → lapse
        _it("bill_from_name", [0, 0, 0, 0, 0, 1, 1, 1]),    # 5/8 wrong → defect
    ]
    tacc = {it.module_key: it.aggregate_accuracy for it in its}
    keep = targeting.disciplined_targets(its, tacc, l_max=5)
    assert "bill_from_name" in keep
    assert "invoice_date" not in keep


def test_targeting_clips_to_topL_by_severity():
    """6 systematic-error modules, worst fully wrong → L=decide_L(1.0,3)=3 →
    only the 3 most-severe are optimized this round."""
    its = [_it(f"m{i}", [0] * (8 - i) + [1] * i) for i in range(6)]  # m0 worst … m5 mildest
    tacc = {it.module_key: it.aggregate_accuracy for it in its}
    keep = targeting.disciplined_targets(its, tacc, l_max=3)
    assert keep == {"m0", "m1", "m2"}


def test_targeting_respects_train_ids():
    """Error counting is restricted to the train split: a field wrong only on a
    VAL sample is not counted as a defect (so it isn't churned on train)."""
    it = _it("currency", [1, 1, 1, 0])  # only s3 wrong
    tacc = {"currency": 0.75}
    # s3 is held out (val) → on train (s0..s2) it's perfect → not a target
    keep = targeting.disciplined_targets([it], tacc, train_ids={"s0", "s1", "s2"})
    assert "currency" not in keep
