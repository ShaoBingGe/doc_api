"""L1 driver integration — the disciplined ReflACT loop end-to-end, token-free.

extract_fn / reflect_fn are mocks: extract_fn's output DEPENDS on the skill doc
(so an applied edit can help/not-help), letting us prove the gate, the bounded
edits (C02), and minibatch grouping (M01) with zero OCR/LLM.
"""
from pathlib import Path

from app.ocr_optimizer.skilltrain import apply as skapply
from app.ocr_optimizer.skilltrain import driver
from app.ocr_optimizer.skilltrain.types import FieldEdit

FIELDS = ["invoiceDate", "currency"]

# 4 train + 2 val pairs; invoiceDate wrong by default, currency always right.
# Names must be globally unique (the extract mock keys train vs val by name).
_TRAIN = [(Path(f"/t/train{i}.pdf"), {"invoiceDate": f"2025-01-0{i}", "currency": "JPY"}) for i in range(1, 5)]
_VAL = [(Path(f"/v/val{i}.pdf"), {"invoiceDate": f"2025-02-0{i}", "currency": "JPY"}) for i in range(1, 3)]


def _make_extract(fix_marker: str, helps_val: bool):
    """extract_fn whose invoiceDate becomes correct only when the skill doc
    contains `fix_marker`; `helps_val` controls whether the fix generalizes to
    the val split (False = an overfitting edit)."""
    gt_by_name = {p.name: gt for p, gt in (_TRAIN + _VAL)}
    train_names = {p.name for p, _ in _TRAIN}

    def extract(pdf, doc):
        gt = gt_by_name[Path(pdf).name]
        pred = {"currency": "JPY", "invoiceDate": "WRONG"}
        if fix_marker in (doc or ""):
            is_train = Path(pdf).name in train_names
            if is_train or helps_val:
                pred["invoiceDate"] = gt["invoiceDate"]
        return pred

    return extract


def test_driver_gate_rejects_overfit():
    """An edit that fixes TRAIN but not VAL is rejected by the held-out gate;
    the skill doc is unchanged and the edit lands in the rejected buffer."""
    extract = _make_extract("OVERFIT_FIX", helps_val=False)
    reflect = lambda f, ex: [FieldEdit(op="append", target=f, content="OVERFIT_FIX")] if f == "invoiceDate" else []
    res = driver.optimize_skill(
        skill_doc="(base skill)", extract_fn=extract, reflect_fn=reflect,
        train_pairs=_TRAIN, val_pairs=_VAL, fields=FIELDS, max_rounds=1,
    )
    assert res.history[-1].action == "reject"
    assert res.best_doc == "(base skill)"          # body unchanged
    assert res.rejected >= 1                        # remembered, won't re-propose


def test_driver_gate_accepts_real_gain():
    """An edit that fixes TRAIN and VAL is accepted; val score rises."""
    extract = _make_extract("GOOD_FIX", helps_val=True)
    reflect = lambda f, ex: [FieldEdit(op="append", target=f, content="GOOD_FIX")] if f == "invoiceDate" else []
    res = driver.optimize_skill(
        skill_doc="(base skill)", extract_fn=extract, reflect_fn=reflect,
        train_pairs=_TRAIN, val_pairs=_VAL, fields=FIELDS, max_rounds=1,
    )
    assert res.history[-1].action == "accept"
    assert "GOOD_FIX" in res.best_doc
    assert res.best_val > res.history[0].val_score   # generalized improvement


def test_SKT_C02_typed_bounded_edit_no_full_rewrite():
    """An accepted round changes only a bounded number of lines (no whole-prompt
    rewrite) — the skilltrain.apply discipline."""
    base = "## [field:currency]\n- 默认 JPY\n"
    extract = _make_extract("GOOD_FIX", helps_val=True)
    reflect = lambda f, ex: [FieldEdit(op="append", target=f, content="GOOD_FIX 规整为 YYYY-MM-DD")] if f == "invoiceDate" else []
    res = driver.optimize_skill(
        skill_doc=base, extract_fn=extract, reflect_fn=reflect,
        train_pairs=_TRAIN, val_pairs=_VAL, fields=FIELDS, max_rounds=1,
    )
    assert res.history[-1].action == "accept"
    assert skapply.diff_line_count(base, res.best_doc) <= 3   # bounded
    assert "默认 JPY" in res.best_doc                          # existing rule preserved


def test_SKT_M01_minibatch_grouped_reflection():
    """5 failing samples on one field → reflect_fn called ONCE for that field
    (minibatch), not once per sample."""
    train5 = [(Path(f"/t/{i}.pdf"), {"invoiceDate": f"2025-03-0{i}", "currency": "JPY"}) for i in range(1, 6)]
    gt_by_name = {p.name: gt for p, gt in (train5 + _VAL)}

    def extract(pdf, doc):
        gt = gt_by_name[Path(pdf).name]
        return {"currency": "JPY", "invoiceDate": "WRONG"}  # always wrong → 1 weak field

    calls = {"n": 0, "batch_sizes": []}

    def reflect(f, examples):
        calls["n"] += 1
        calls["batch_sizes"].append(len(examples))
        return []

    driver.optimize_skill(
        skill_doc="x", extract_fn=extract, reflect_fn=reflect,
        train_pairs=train5, val_pairs=_VAL, fields=FIELDS, max_rounds=1,
    )
    assert calls["n"] == 1                      # one weak field → one grouped call
    assert calls["batch_sizes"] == [5]          # all 5 failing samples in one batch
