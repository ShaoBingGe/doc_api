"""Disciplined ReflACT skill-optimization loop (ADR-001 P1).

Orchestrates the pure mechanisms into one loop, over INJECTED functions so the
whole thing is unit-testable with zero OCR/LLM:

  extract_fn(pdf_path, skill_doc) -> entity dict      (real OCR, or a mock)
  reflect_fn(field, examples)     -> list[FieldEdit]  (real LLM, or a mock)

Per round:
  1. score train rollouts (extract + score each train sample)
  2. pick weak fields (accuracy < 1.0), reflect each as a minibatch → edits
  3. aggregate (support-count) → drop rejected (buffer) → clip top-L (LR by severity)
  4. classify SKILL_DEFECT vs EXECUTION_LAPSE — only DEFECT edits touch the body
  5. apply DEFECT edits → candidate skill doc
  6. GATE on held-out val: accept iff val soft strictly improves, else keep
     current and remember the rejected edits

The deployed artifact is `best_doc` (the validated skill). This loop is the
logic later wired into run_orchestrator; here it stands alone so it can be
proven on Japan-inv without touching production.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Callable

from . import apply as _apply
from . import aggregate as _agg
from . import classify as _classify
from . import clip as _clip
from . import gate as _gate
from .buffer import RejectedEditBuffer
from .types import FieldEdit

ExtractFn = Callable[[object, str], dict]          # (pdf_path, skill_doc) -> entity
ReflectFn = Callable[[str, list], list]            # (field, examples) -> list[FieldEdit]


@dataclass
class RoundLog:
    round: int
    action: str                      # "init" | "accept" | "reject"
    val_score: float
    train_score: float = 0.0
    edits: int = 0
    weak_fields: list = dc_field(default_factory=list)


@dataclass
class TrainResult:
    best_doc: str
    best_val: float
    history: list                    # list[RoundLog]
    rejected: int                    # size of the rejected-edit buffer


def _score_doc(extract_fn, pairs, doc, fields) -> float:
    """Mean soft field accuracy of `doc` over pairs (token-free when extract_fn
    is mocked)."""
    from app.ocr_optimizer.eval.bench_japan_inv import score_pred

    if not pairs:
        return 0.0
    softs = []
    for pdf, gt in pairs:
        pred = extract_fn(pdf, doc) or {}
        softs.append(score_pred(pred, gt, fields)["soft"])
    return sum(softs) / len(softs)


def optimize_skill(
    *,
    skill_doc: str,
    extract_fn: ExtractFn,
    reflect_fn: ReflectFn,
    train_pairs: list,
    val_pairs: list,
    fields: list[str],
    max_rounds: int = 3,
    l_max: int = 5,
) -> TrainResult:
    buf = RejectedEditBuffer()
    best_doc = skill_doc
    best_val = _score_doc(extract_fn, val_pairs, best_doc, fields)
    history = [RoundLog(round=0, action="init", val_score=round(best_val, 4))]

    for r in range(1, max_rounds + 1):
        from app.ocr_optimizer.eval.bench_japan_inv import score_pred

        # 1) extract train ONCE with the current best doc (cache → no re-OCR),
        #    score, find weak fields + per-field error counts.
        train_preds = [extract_fn(pdf, best_doc) or {} for pdf, _gt in train_pairs]
        rows = [score_pred(p, gt, fields) for p, (_pdf, gt) in zip(train_preds, train_pairs)]
        n = len(rows) or 1
        train_soft = sum(rw["soft"] for rw in rows) / n
        err_count = {f: sum(0 if rw["per_field"][f]["hard"] else 1 for rw in rows) for f in fields}
        weak = sorted((f for f in fields if err_count[f] > 0), key=lambda f: -err_count[f])

        # 2) reflect each weak field as a minibatch (ONE reflect call per field,
        #    over its failing samples) → candidate edits. Uses cached preds.
        candidates: list[FieldEdit] = []
        for f in weak:
            examples = [
                {"gt": gt.get(f), "pred": p.get(f)}
                for p, rw, (_pdf, gt) in zip(train_preds, rows, train_pairs)
                if not rw["per_field"][f]["hard"]
            ]
            for e in (reflect_fn(f, examples) or []):
                # tag kind from the error's systematic-ness (defect vs lapse)
                if not e.kind:
                    e.kind = _classify.classify(err_count[f], n)
                candidates.append(e)

        # 3) aggregate (support) → drop already-rejected → clip top-L (LR by severity)
        merged = _agg.aggregate_edits(candidates)
        fresh = buf.filter(merged)
        L = _clip.decide_L(1.0 - train_soft, l_max=l_max)
        selected = _clip.rank_and_select(fresh, L)

        # 4) only SKILL_DEFECT edits modify the body; lapses are not applied here
        body_edits = [e for e in selected if e.kind == "SKILL_DEFECT"]

        # 5) apply → candidate doc
        cand_doc = _apply.apply_edits(best_doc, body_edits)

        # 6) GATE on held-out val
        cand_val = _score_doc(extract_fn, val_pairs, cand_doc, fields)
        decision = _gate.decide(best_val, cand_val)
        if decision.accepted:
            best_doc, best_val = cand_doc, cand_val
            history.append(RoundLog(round=r, action="accept", val_score=round(cand_val, 4),
                                    train_score=round(train_soft, 4), edits=len(body_edits),
                                    weak_fields=weak[:5]))
        else:
            buf.add_all(body_edits)  # never re-propose a rejected edit
            history.append(RoundLog(round=r, action="reject", val_score=round(cand_val, 4),
                                    train_score=round(train_soft, 4), edits=len(body_edits),
                                    weak_fields=weak[:5]))

    return TrainResult(best_doc=best_doc, best_val=round(best_val, 4), history=history, rejected=len(buf))
