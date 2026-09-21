"""
Core eval harness — pure scoring + OCR-driving wrappers.

Design (so Phase 0 is testable offline, without Gemini):
  - `score_outputs(...)` is PURE: given already-produced OCR outputs + ground
    truth, it slices per module and scores with the production evaluator. No
    OCR, no DB, fully deterministic — this is what the unit test exercises.
  - `evaluate_prompt(...)` = run OCR via ocr_runner, then `score_outputs`.
  - `benchmark_ab(...)` runs two prompts over identical inputs → per-module
    delta (the with/without comparison).

Scoring is intentionally identical to run_orchestrator._run_one_round step 2
(slicer.extract → evaluator.compare → mean field_accuracy) so harness numbers
match what the real iteration sees.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..service import evaluator, ground_truth, slicer


# ── Lightweight, ORM-decoupled module spec ────────────────────────────────────

@dataclass
class ModuleSpec:
    """The minimum a module needs to be scored: where its value lives
    (json_path) and the schema fragment used by the evaluator."""
    module_key: str
    json_path: str
    schema_fragment: dict | None = None
    display_name: str | None = None


def module_specs_from_orm(modules: list) -> list[ModuleSpec]:
    """Build ModuleSpecs from OcrModule ORM rows (or any objects exposing the
    same attributes). Kept separate so `score_outputs` never depends on the ORM."""
    out: list[ModuleSpec] = []
    for m in modules:
        out.append(ModuleSpec(
            module_key=getattr(m, "module_key", "") or "",
            json_path=getattr(m, "json_path", "") or "",
            schema_fragment=getattr(m, "schema_fragment", None),
            display_name=getattr(m, "display_name", None),
        ))
    return out


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ModuleScore:
    module_key: str
    accuracy: float                       # mean field_accuracy across samples
    matched_count: int                    # samples scoring >= 0.999
    sample_count: int
    per_sample: list[dict] = field(default_factory=list)
    display_name: str | None = None


@dataclass
class EvalReport:
    overall_accuracy: float               # mean of module accuracies
    module_scores: list[ModuleScore]
    sample_count: int
    module_count: int
    ocr_error_doc_ids: list[str] = field(default_factory=list)

    def module_map(self) -> dict[str, ModuleScore]:
        return {m.module_key: m for m in self.module_scores}

    def to_dict(self) -> dict:
        return {
            "overall_accuracy": round(self.overall_accuracy, 4),
            "sample_count": self.sample_count,
            "module_count": self.module_count,
            "ocr_error_doc_ids": self.ocr_error_doc_ids,
            "modules": [
                {
                    "module_key": m.module_key,
                    "display_name": m.display_name,
                    "accuracy": round(m.accuracy, 4),
                    "matched": f"{m.matched_count}/{m.sample_count}",
                }
                for m in self.module_scores
            ],
        }


# ── Pure scoring ──────────────────────────────────────────────────────────────

def _is_empty_slice(v: Any) -> bool:
    """True when a sliced value carries NO real GT (None, "", or a container of
    only-empties). Strict golden scoring SKIPS such (doc, module) pairs so a
    null-GT field can never produce a false pass (req 1)."""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, list):
        return all(_is_empty_slice(x) for x in v)
    if isinstance(v, dict):
        return all(_is_empty_slice(x) for x in v.values())
    return False


# ── strict 模式的行表（明细数组）专用判据 ────────────────────────────────────
# 历史问题：line_items / tax_summary 在 strict 模式下与标量字段同用
# `ocr_sliced == gt_sliced` 整体全等——每行每字段任一处差异（浮点位数、
# 描述文字、单位写法）即判 0。实测 8 次黄金批全部 0/5，判别力为零，
# 「line_items 不得下降」的门在它身上是空条件。
# 改为：**行数一致（gate）+ 逐行关键字段匹配率（分值）**。
# 关键字段限定为语义硬字段；description / unitOfMeasure 这类 GT 本身与
# 模板规则冲突的模糊字段不参与（GT 把品名复制进了 description，而模板
# §1.35 要求那种情况 description 留空——比它等于惩罚正确行为）。

_ROW_KEY_FIELDS = (
    "articleName", "quantity", "unitPrice", "netAmount",       # 商品行
    "taxCategory", "taxRate", "tax", "netTaxableAmount",       # 税表行
)


def _flatten_rows(x: Any) -> Any:
    """[[row,...], ...]（每张票一个子列表）→ [row,...]；已扁平则原样返回。"""
    if isinstance(x, list) and x and all(isinstance(e, list) for e in x):
        return [r for sub in x for r in sub]
    return x


def _is_row_table(x: Any) -> bool:
    x = _flatten_rows(x)
    return isinstance(x, list) and bool(x) and all(isinstance(r, dict) for r in x)


def _row_field_eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and not isinstance(a, bool) or \
       isinstance(b, (int, float)) and not isinstance(b, bool):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(a or "").strip() == str(b or "").strip()


def _score_row_table_strict(got: Any, expected: Any) -> tuple[float, bool, str]:
    """→ (accuracy, matched, diff)。行数不等直接 0（gate）；行数相等时
    按位置逐行比关键字段，accuracy = 各行匹配率均值。"""
    exp = _flatten_rows(expected)
    g = _flatten_rows(got)
    if not isinstance(g, list) or not all(isinstance(r, dict) for r in g):
        return 0.0, False, "row-table-shape-mismatch"
    if len(g) != len(exp):
        return 0.0, False, f"row-count-mismatch got={len(g)} expected={len(exp)}"
    if not exp:
        return 1.0, True, ""
    per_row: list[float] = []
    for er, gr in zip(exp, g):
        keys = [k for k in _ROW_KEY_FIELDS if er.get(k) not in ("", None)]
        if not keys:   # GT 行没有任何关键字段 → 退回比 GT 行的全部非空字段
            keys = [k for k, v in er.items() if v not in ("", None)]
        if not keys:
            per_row.append(1.0)
            continue
        per_row.append(sum(_row_field_eq(gr.get(k), er[k]) for k in keys) / len(keys))
    acc = sum(per_row) / len(per_row)
    matched = acc > 0.999
    return acc, matched, ("" if matched else f"row-fields-partial {acc:.2f}")


def score_outputs(
    modules: list[ModuleSpec],
    ocr_outputs: dict[str, Any],
    ground_truths: dict[str, dict],
    *,
    strict: bool = False,
) -> EvalReport:
    """Score already-produced OCR outputs against ground truth, per module.

    Args:
        modules: list of ModuleSpec.
        ocr_outputs: {str(doc_id): parsed_json}. A value shaped
            {"_error": "..."} marks an OCR failure for that doc (scored 0 and
            its id collected in ocr_error_doc_ids).
        ground_truths: {str(doc_id): gt_json}.
        strict: golden-gate mode (req 2). A field passes ONLY on EXACT equality
            (no date/number/format fuzzing, no partial credit) — pass=1.0 else
            0.0. (doc, module) pairs whose GT slice is empty are SKIPPED so a
            null GT never yields a false pass (req 1). The per-sample record
            carries `expected` / `got` so deviations can feed the
            diff→reflect→optimize loop (req 3).

    Non-strict (default) keeps the production fuzzy scoring
    (evaluator.compare); a module's accuracy is the mean across samples and the
    overall accuracy the mean across modules (matching _run_one_round).
    """
    doc_ids = list(ground_truths.keys())
    ocr_error_doc_ids = [
        d for d in doc_ids
        if isinstance(ocr_outputs.get(d), dict) and "_error" in ocr_outputs[d]
    ]

    module_scores: list[ModuleScore] = []
    for spec in modules:
        per_sample: list[dict] = []
        for d in doc_ids:
            # Align GT root to the json_path (dict GT + "$[*]." path → wrap in
            # list). No-op for already-list golden GT. See align_for_path.
            gt_sliced = slicer.extract(
                ground_truth.align_for_path(ground_truths.get(d), spec.json_path),
                spec.json_path,
            )
            ocr_full = ocr_outputs.get(d)

            if strict and _is_empty_slice(gt_sliced):
                # GT doesn't cover this field on this seed → not tested here.
                continue

            if isinstance(ocr_full, dict) and "_error" in ocr_full:
                if strict:
                    # 批次6：黄金 strict 路径同样剔除传输失败——0 分会把一次
                    # 网络抖动误读成「机器回归」，拦下无辜的平台 PR。失败
                    # doc 已收进 ocr_error_doc_ids，调用方据此判定批次有效性。
                    continue
                per_sample.append({
                    "doc_id": d, "accuracy": 0.0, "matched": False,
                    "diff": f"OCR error: {str(ocr_full.get('_error'))[:120]}",
                    "expected": gt_sliced, "got": None,
                })
                continue

            ocr_sliced = slicer.extract(ocr_full, spec.json_path)

            if strict:
                if _is_row_table(gt_sliced):
                    # 行表（明细/税表数组）：行数 gate + 逐行关键字段匹配率。
                    # 整体全等对它是空判据（见 _score_row_table_strict 注释）。
                    acc, passed, diff = _score_row_table_strict(ocr_sliced, gt_sliced)
                    per_sample.append({
                        "doc_id": d, "accuracy": acc, "matched": passed,
                        "diff": diff, "expected": gt_sliced, "got": ocr_sliced,
                    })
                    continue
                # Exact equality — any field/char/format deviation fails (req 2).
                passed = ocr_sliced == gt_sliced
                per_sample.append({
                    "doc_id": d,
                    "accuracy": 1.0 if passed else 0.0,
                    "matched": passed,
                    "diff": "" if passed else "exact-mismatch",
                    "expected": gt_sliced, "got": ocr_sliced,
                })
            else:
                matched, acc, diff = evaluator.compare(
                    ocr_sliced, gt_sliced, spec.schema_fragment,
                )
                # expected/got 同 strict 分支：模糊评分的消费方（_run_one_round
                # 的 OcrModuleIteration → optimizer 失败样本上下文）需要切片。
                per_sample.append({
                    "doc_id": d, "accuracy": acc, "matched": matched, "diff": diff,
                    "expected": gt_sliced, "got": ocr_sliced,
                })

        # In strict mode a module with no tested samples (GT empty everywhere)
        # is excluded from the score rather than counted as 0.
        if strict and not per_sample:
            continue

        accs = [p["accuracy"] for p in per_sample]
        mod_acc = statistics.fmean(accs) if accs else 0.0
        module_scores.append(ModuleScore(
            module_key=spec.module_key,
            display_name=spec.display_name,
            accuracy=mod_acc,
            matched_count=sum(1 for p in per_sample if p["matched"]),
            sample_count=len(per_sample),
            per_sample=per_sample,
        ))

    overall = (
        statistics.fmean([m.accuracy for m in module_scores])
        if module_scores else 0.0
    )
    return EvalReport(
        overall_accuracy=overall,
        module_scores=module_scores,
        sample_count=len(doc_ids),
        module_count=len(module_scores),
        ocr_error_doc_ids=ocr_error_doc_ids,
    )


def collect_deviations(report: EvalReport) -> list[dict]:
    """Extract every failing field from a STRICT report as deviation records,
    shaped for the diff→reflect→optimize loop (req 3):
        {module_key, doc_id, expected, got}
    Only meaningful on a report produced with strict=True."""
    out: list[dict] = []
    for m in report.module_scores:
        for p in m.per_sample:
            if not p.get("matched", False):
                out.append({
                    "module_key": m.module_key,
                    "doc_id": p.get("doc_id"),
                    "expected": p.get("expected"),
                    "got": p.get("got"),
                })
    return out


# ── OCR-driving wrappers ──────────────────────────────────────────────────────

def evaluate_prompt(
    db,
    *,
    modules: list[ModuleSpec],
    sample_doc_ids: list[uuid.UUID],
    composed_prompt: str,
    composed_schema: dict | None,
    ground_truths: dict[str, dict],
    processor_spec: str = "mock",
    model_name: str | None = None,
    strict: bool = False,
) -> EvalReport:
    """Run `composed_prompt` over `sample_doc_ids` and score per module.

    `strict=True` uses the golden-gate exact-match scorer (see score_outputs).
    Read-only: uses ocr_runner.run_ocr_on_samples (which does not persist) and
    never writes to the DB. Safe against live ApiDefs.
    """
    from ..service import ocr_runner

    ocr_outputs = ocr_runner.run_ocr_on_samples(
        db,
        sample_document_ids=sample_doc_ids,
        composed_prompt=composed_prompt,
        composed_schema=composed_schema,
        processor_spec=processor_spec,
        model_name=model_name,
    )
    return score_outputs(modules, ocr_outputs, ground_truths, strict=strict)


def benchmark_ab(
    db,
    *,
    modules_a: list[ModuleSpec],
    modules_b: list[ModuleSpec],
    sample_doc_ids: list[uuid.UUID],
    prompt_a: str,
    schema_a: dict | None,
    prompt_b: str,
    schema_b: dict | None,
    ground_truths: dict[str, dict],
    processor_spec: str = "mock",
    model_name: str | None = None,
) -> dict:
    """Run two prompts over identical inputs and report the per-module +
    overall delta (B − A). Use A = current/active prompt, B = candidate.

    modules_a / modules_b may differ (a refactor can rename/restructure
    modules); deltas are keyed by module_key and only computed for keys
    present in both reports.
    """
    report_a = evaluate_prompt(
        db, modules=modules_a, sample_doc_ids=sample_doc_ids,
        composed_prompt=prompt_a, composed_schema=schema_a,
        ground_truths=ground_truths,
        processor_spec=processor_spec, model_name=model_name,
    )
    report_b = evaluate_prompt(
        db, modules=modules_b, sample_doc_ids=sample_doc_ids,
        composed_prompt=prompt_b, composed_schema=schema_b,
        ground_truths=ground_truths,
        processor_spec=processor_spec, model_name=model_name,
    )

    a_map = report_a.module_map()
    b_map = report_b.module_map()
    per_module_delta = {
        k: round(b_map[k].accuracy - a_map[k].accuracy, 4)
        for k in a_map.keys() & b_map.keys()
    }
    return {
        "a": report_a.to_dict(),
        "b": report_b.to_dict(),
        "overall_delta": round(
            report_b.overall_accuracy - report_a.overall_accuracy, 4,
        ),
        "per_module_delta": per_module_delta,
        "regressed_modules": sorted(
            k for k, d in per_module_delta.items() if d < -1e-9
        ),
    }
