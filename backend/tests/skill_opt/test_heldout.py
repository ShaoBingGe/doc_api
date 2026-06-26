"""L1 — held-out split helpers wired into run_orchestrator (token-free).

These back the SKILL_HELDOUT_GATE production wiring: optimize on TRAIN, select
versions on VAL. Pure logic; the flag-OFF regression is covered by the existing
round tests (test_golden_loop / test_monotonic_finalize / test_field_accuracy_timeline).
"""
from types import SimpleNamespace

from app.ocr_optimizer.skilltrain import heldout


def test_heldout_val_ids_trailing_split():
    """Trailing `frac` are val; anchors (leading) stay in train; never all."""
    ids = [f"s{i}" for i in range(12)]
    val = heldout.val_ids(ids, frac=0.25)
    assert val == ids[-3:]                       # last 3 = val
    assert set(ids[:9]).isdisjoint(val)          # anchors in train
    # tiny sets degrade safely
    assert heldout.val_ids(["a"]) == []          # n<=1 → no val
    assert len(heldout.val_ids(["a", "b", "c", "d"], frac=0.25)) == 1
    assert len(heldout.val_ids(ids, frac=0.9)) == 10   # int(12*0.9)=10
    assert len(heldout.val_ids(ids, frac=1.0)) == 11   # clamp: always leave ≥1 train


def test_heldout_split_accuracy():
    """Module perfect on train but wrong on val → train_acc high (not a target),
    overall VAL accuracy low (so version selection penalizes the overfit)."""
    it = SimpleNamespace(
        module_key="invoiceDate", aggregate_accuracy=0.66,
        per_sample_results=[
            {"sample_doc_id": "t1", "field_accuracy": 1.0},
            {"sample_doc_id": "t2", "field_accuracy": 1.0},
            {"sample_doc_id": "v1", "field_accuracy": 0.0},
        ],
    )
    overall_val, target_train = heldout.split_accuracy([it], {"v1"})
    assert overall_val == 0.0                     # val-only score
    assert target_train["invoiceDate"] == 1.0     # train-only → optimizer sees "done"


def test_heldout_split_accuracy_empty_side_falls_back():
    """A split with no samples on one side falls back to the all-sample aggregate
    (defensive — never crashes a round)."""
    it = SimpleNamespace(
        module_key="currency", aggregate_accuracy=0.8,
        per_sample_results=[{"sample_doc_id": "t1", "field_accuracy": 0.8}],
    )
    overall_val, target_train = heldout.split_accuracy([it], {"v_absent"})
    assert overall_val == 0.8                      # no val samples → fall back
    assert target_train["currency"] == 0.8
