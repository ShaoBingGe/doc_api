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


def country_scalar_fields(country: str = "JP") -> list[str]:
    """The scalar fields the country TEMPLATE actually defines — the fair basis
    for measuring product capability (scoring fields the template never asks for
    artificially deflates accuracy). Derived from the template decomposition;
    arrays/objects (page, line items, tax summary) excluded.
    """
    from app.ocr_optimizer.service import template_loader

    d = template_loader.decompose_country_template(country)
    out: list[str] = []
    for m in d.get("modules", []):
        frag = m.get("schema_fragment") or {}
        if (frag.get("type") or "STRING").upper() in {"ARRAY", "OBJECT"}:
            continue
        leaf = (m.get("json_path") or "").split(".")[-1]
        leaf = leaf.replace("[*]", "").replace("[", "").replace("]", "").strip()
        if leaf and leaf not in {"$", "page"}:
            out.append(leaf)
    return out


def gt_union_keys(pairs: list["Pair"]) -> set[str]:
    """Union of all top-level keys present across a split's GT entities."""
    keys: set[str] = set()
    for _pdf, gt in pairs:
        if isinstance(gt, dict):
            keys |= set(gt.keys())
    return keys


def fair_fields(country: str, pairs: list["Pair"]) -> list[str]:
    """The HONEST scoring set = template-defined scalar fields ∩ fields the GT
    actually tracks. Excludes both (a) template fields the GT has no truth for
    (e.g. invoiceType — present in the template, absent from 海信 labels →
    would penalize a correct extraction) and (b) GT fields the template never
    asks for. This is the fair product-capability measure.
    """
    gt_keys = gt_union_keys(pairs)
    return [f for f in country_scalar_fields(country) if f in gt_keys]


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


# ── Real-OCR predictor (mirrors the product's v1 country-template extraction) ─
#
# Token-COSTING: only the manual baseline/validation runner calls this; L0/L1
# tests never do. Building it is free; running predict() issues one VLM call.

def _parse_entity(raw_text: str) -> dict:
    """Parse a processor's raw text into a single invoice entity dict.

    Handles: a top-level JSON ARRAY (the country schema shape → first element),
    a ```json fenced block, or a {"entities": [...]} wrapper. Pure / token-free.
    """
    import json

    from app.processors.base import extract_json

    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        blocks = extract_json(raw_text or "")
        try:
            parsed = json.loads(blocks[0]) if blocks else {}
        except (json.JSONDecodeError, IndexError):
            parsed = {}
    if isinstance(parsed, list):
        return parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    if isinstance(parsed, dict):
        ents = parsed.get("entities")
        if isinstance(ents, list):
            return ents[0] if ents and isinstance(ents[0], dict) else {}
        return parsed
    return {}


def build_country_prompt(country: str = "JP") -> tuple[str, dict]:
    """Build the v1-style composed prompt + schema for a country (same shape
    preset_init uses for a fresh API). Returns (prompt, json_schema)."""
    from app.ocr_optimizer.service import template_loader
    from app.ocr_optimizer.service.composer import GLOBAL_OUTPUT_CONTRACT_DETAILS

    d = template_loader.decompose_country_template(country)
    prompt = d["prompt_format"].rstrip() + "\n\n" + GLOBAL_OUTPUT_CONTRACT_DETAILS + "\n"
    return prompt, d["json_schema"]


def real_predictor(country: str = "JP", processor_type: str = "qwen") -> PredictFn:
    """A predict_fn that runs the real country-template extraction on a PDF.

    Used ONLY by the manual baseline/validation runner (issues real VLM calls).
    Mirrors production v1 extraction so the bench measures actual product
    capability.
    """
    from app.processors.factory import ProcessorFactory

    prompt, schema = build_country_prompt(country)
    proc = ProcessorFactory.create(processor_type)

    def predict(pdf_path: Path) -> dict:
        raw = proc.process_document(str(pdf_path), prompt, {"schema": schema})
        return _parse_entity(raw)

    return predict


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
