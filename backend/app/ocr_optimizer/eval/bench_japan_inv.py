"""Japan-inv benchmark harness (P0').

Measures field-level extraction accuracy on the Japan-inv corpus (train/val/test
split 5:2:3, seed=42) so we can quantify the skill-optimization rework's effect:
the three numbers in docs/skill-optimization-plan.md §2.3 —
  1. test field accuracy,
  2. train-test gap (overfitting),
  3. OCR call count.

TOKEN-FREE CORE: every function here takes an injected ``predict_fn(pdf_path) ->
entity_dict``. The real-OCR predictor is supplied ONLY by the (manual/nightly)
baseline runner; L0/L1 tests pass a mock predictor and call zero VLM. Scoring
reuses the existing zero-tolerance scorer (``evaluator.compare``) so the bench
agrees with production accuracy.

This module is a dev/measurement tool — it is NOT imported by any production
request path.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from statistics import mean
from typing import Any, Callable

# Repo root: backend/app/ocr_optimizer/eval/bench_japan_inv.py → parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]
JAPAN_INV = _REPO_ROOT / "Japan-inv"

# Platform-canonical JP scalar fields scored by the bench. Names align 1:1 with
# the Japan-inv GT entity keys (no mapping layer). Arrays (detailOfGoodsOrServices
# / detailOfTaxSummary) are excluded from this scalar harness; add later if needed.
CANON_JP_FIELDS: list[str] = [
    "docType",
    "nameOfInvoice",
    "invoiceNumber",
    "invoiceDate",
    "totalNetAmount",
    "totalAmount",
    "totalTaxAmount",
    "currency",
    "billToName",
    "billFromName",
    "billFromTaxIdentificationNumber",
    "dueDate",
]

Pair = tuple[Path, dict]
PredictFn = Callable[[Path], dict]


# ── Corpus loading ────────────────────────────────────────────────────────────

def available() -> bool:
    """True when the (gitignored, local) Japan-inv corpus is present."""
    return (JAPAN_INV / "train" / "labels").is_dir()


def load(split: str, k: int | None = None, seed: int = 42) -> list[Pair]:
    """Load `(pdf_path, gt_entity)` pairs from `Japan-inv/<split>/{docs,labels}`.

    `labels/<X>.pdf.json` pairs with `docs/<X>.pdf`. When `k` is given, return a
    deterministic seeded sample of size k (stable order). Uses the FIRST entity
    of each label (multi-invoice docs score their primary entity).
    """
    docs_dir = JAPAN_INV / split / "docs"
    labels_dir = JAPAN_INV / split / "labels"
    pairs: list[Pair] = []
    for label_path in sorted(labels_dir.glob("*.pdf.json")):
        pdf_name = label_path.name[: -len(".json")]  # "<X>.pdf.json" → "<X>.pdf"
        pdf_path = docs_dir / pdf_name
        if not pdf_path.exists():
            continue
        try:
            data = json.loads(label_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entities = data.get("entities") or []
        entity = entities[0] if entities else {}
        pairs.append((pdf_path, entity))
    if k is not None and k < len(pairs):
        pairs = random.Random(seed).sample(pairs, k)
        pairs.sort(key=lambda p: p[0].name)  # stable order; set is what matters
    return pairs


# ── Scoring (reuses evaluator.compare — zero-tolerance) ───────────────────────

def score_pred(pred: dict, gt: dict, fields: list[str] = CANON_JP_FIELDS) -> dict:
    """Field-level score of one predicted entity vs GT entity.

    Per field: hard = exact-match (evaluator.compare matched), soft = recursive
    accuracy. Returns {hard, soft, per_field}. Fields absent on both sides count
    as correct (both empty); present-vs-missing counts as wrong.
    """
    from app.ocr_optimizer.service import evaluator  # lazy: dodge cold-start cycle

    per_field: dict[str, dict] = {}
    hard_sum = 0.0
    soft_sum = 0.0
    for f in fields:
        matched, acc, _diff = evaluator.compare(pred.get(f), gt.get(f))
        per_field[f] = {"hard": bool(matched), "soft": acc}
        hard_sum += 1.0 if matched else 0.0
        soft_sum += acc
    n = len(fields) or 1
    return {"hard": hard_sum / n, "soft": soft_sum / n, "per_field": per_field}


def run_split(
    predict_fn: PredictFn, pairs: list[Pair], fields: list[str] = CANON_JP_FIELDS
) -> dict:
    """Run a predictor over a split's pairs → aggregate hard/soft accuracy +
    OCR call count (== number of predict_fn invocations)."""
    hards: list[float] = []
    softs: list[float] = []
    calls = 0
    for pdf_path, gt in pairs:
        pred = predict_fn(pdf_path) or {}
        calls += 1
        s = score_pred(pred, gt, fields)
        hards.append(s["hard"])
        softs.append(s["soft"])
    return {
        "hard": mean(hards) if hards else 0.0,
        "soft": mean(softs) if softs else 0.0,
        "n": len(pairs),
        "ocr_calls": calls,
    }


def run_bench(
    predict_fn: PredictFn,
    *,
    k_train: int = 12,
    val_k: int | None = 15,
    seed: int = 42,
    fields: list[str] = CANON_JP_FIELDS,
) -> dict:
    """The three numbers: train(k_train) / val(val_k) / test(full) field accuracy,
    train-test gap, total OCR calls. `predict_fn` is injected (mock in tests,
    real OCR in the manual baseline runner)."""
    train = load("train", k=k_train, seed=seed)
    val = load("val", k=val_k, seed=seed)
    test = load("test")  # full 108 — the held-out final judge
    tr = run_split(predict_fn, train, fields)
    va = run_split(predict_fn, val, fields)
    te = run_split(predict_fn, test, fields)
    return {
        "train": tr,
        "val": va,
        "test": te,
        "gap_hard": round(tr["hard"] - te["hard"], 4),
        "gap_soft": round(tr["soft"] - te["soft"], 4),
        "ocr_calls": tr["ocr_calls"] + va["ocr_calls"] + te["ocr_calls"],
        "fields": list(fields),
        "seed": seed,
    }


# ── OCR result cache (skip re-OCR of an unchanged (sample, skill) pair) ────────

class CachingPredictor:
    """Wrap a predictor with a `(pdf, skill_version)`-keyed cache so re-running
    the bench (same skill trajectory) reuses outputs instead of re-OCR'ing.

    Exposes `.calls` (underlying invocations) and `.hits` (cache hits) for the
    L4 cost assertions.
    """

    def __init__(self, predict_fn: PredictFn, skill_version: str):
        self._fn = predict_fn
        self._ver = skill_version
        self._cache: dict[tuple[str, str], Any] = {}
        self.calls = 0
        self.hits = 0

    def __call__(self, pdf_path: Path) -> dict:
        key = (str(pdf_path), self._ver)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.calls += 1
        out = self._fn(pdf_path) or {}
        self._cache[key] = out
        return out
