"""
CLAUDE.md §④ — round-over-round monotonic accuracy guard.

finalize must activate the best-EVALUATED version, not the latest round's
un-evaluated output, so a round's "optimization" can never regress the active
prompt below the starting version.
"""

from __future__ import annotations

import uuid


def _make_run_with_rounds(db, evaluated):
    """evaluated = [(version_id, accuracy), ...] in round order (round 1..N).
    Round i recorded `accuracy` for the version it evaluated at entry
    (prompt_version_id = version_id)."""
    from app.ocr_optimizer.models import (
        OcrOptimizationRun, OcrOptimizationRound, RunStatus, RoundPhase,
    )
    run = OcrOptimizationRun(
        id=uuid.uuid4(),
        api_definition_id=uuid.uuid4(),
        starting_version_id=evaluated[0][0],
        status=RunStatus.running.value,
        sample_document_ids=[],
        llm_provider="mock|",
    )
    db.add(run)
    db.flush()
    for i, (pv, acc) in enumerate(evaluated, start=1):
        db.add(OcrOptimizationRound(
            id=uuid.uuid4(), run_id=run.id, round_num=i,
            prompt_version_id=pv, overall_accuracy=acc,
            phase=RoundPhase.completed.value,
        ))
    db.commit()
    return run


def test_best_evaluated_picks_argmax_not_latest(db_session):
    """Accuracy went up then DOWN: 0.70 → 0.90 → 0.85. The guard must pick the
    0.90 version, NOT the latest (0.85) — no regression."""
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version
    vstart, v1, v2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run = _make_run_with_rounds(db_session, [(vstart, 0.70), (v1, 0.90), (v2, 0.85)])
    best_id, best_acc = _best_evaluated_version(db_session, run.id)
    assert best_id == v1
    assert best_acc == 0.90


def test_best_evaluated_is_monotonic_when_improving(db_session):
    """Normal improving run: the best evaluated is the latest evaluated."""
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version
    vstart, v1, v2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run = _make_run_with_rounds(db_session, [(vstart, 0.70), (v1, 0.85), (v2, 0.97)])
    best_id, best_acc = _best_evaluated_version(db_session, run.id)
    assert best_id == v2 and best_acc == 0.97


def test_detect_module_regressions():
    """Per-field regression detector (req 3): flags only modules whose accuracy
    dropped vs the previous round."""
    from app.ocr_optimizer.service.run_orchestrator import detect_module_regressions
    prev = {"invoice_number": 0.9, "currency": 1.0, "saler_name": 0.6, "po": 0.5}
    curr = {"invoice_number": 0.7,   # regressed
            "currency": 1.0,         # unchanged
            "saler_name": 0.8,       # improved
            "new_field": 0.4}        # no prior → not a regression
    regressed = detect_module_regressions(prev, curr)
    assert regressed == ["invoice_number"]


def test_detect_module_regressions_empty_when_no_prior():
    from app.ocr_optimizer.service.run_orchestrator import detect_module_regressions
    assert detect_module_regressions({}, {"a": 0.5}) == []


def test_best_evaluated_never_below_start(db_session):
    """Even if every later round regressed, the guard falls back to the
    starting version — activated accuracy is never below the start."""
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version
    vstart, v1, v2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    run = _make_run_with_rounds(db_session, [(vstart, 0.92), (v1, 0.80), (v2, 0.75)])
    best_id, best_acc = _best_evaluated_version(db_session, run.id)
    assert best_id == vstart and best_acc == 0.92
