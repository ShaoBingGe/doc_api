"""版本选择 / 单调守护（红线④ 的落点，customer_iteration 拆分第一刀）.

客户回路 3 轮迭代结束后「激活哪个版本」的全部决策逻辑：

  - `_best_evaluated_version`  —— 单调守护：只在**已评估**的版本里取 argmax，
    带平局带（批次6：观测分差小于半个量化步长是采样噪声，持平保早版）；
  - `_confirm_version_accuracy` —— Phase-4a 终轮确认：末轮产物未评估，
    用一次 OCR 确认后才允许参选（含批次2 的降级/失败样本剔除）；
  - `_latest_round_version` / `_tie_band` —— 支撑查询与量化步长。

独立成模块的原因（结构审查 1.1）：这是「准确率永不下降」承诺的实现处，
必须能被单测直接打靶（test_monotonic_finalize / test_stability_guards /
test_eval_validity 均直接 import）；此前埋在 2400 行的 customer_iteration
里。函数名保持原样（含下划线），customer_iteration 作 facade 重导出，
调用方与测试零改动。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from ..models import OcrModule, OcrOptimizationRound, OcrPromptVersion

logger = logging.getLogger(__name__)


def _latest_round_version(db: Session, run_id: uuid.UUID) -> uuid.UUID | None:
    r = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run_id)
        .order_by(OcrOptimizationRound.round_num.desc())
        .first()
    )
    return r.next_version_id if r and r.next_version_id else None


def _confirm_version_accuracy(db: Session, run, version_id: uuid.UUID) -> float | None:
    """Phase-4a「终轮确认评估」: OCR the run's confirmed samples with `version_id`'s
    composed_prompt and return its overall accuracy (fuzzy, same scoring path as
    the rounds). Lets the monotonic guard ALSO consider the last round's
    un-evaluated output, so a genuine final-round gain isn't discarded.

    Costs ONE OCR pass. Read-only (no version/round writes). Returns None on any
    failure (caller then keeps the best already-evaluated version → still
    monotonic). Builds on align_for_path (real accuracy).
    """
    from app.models.api_definition import ApiDefinition

    from ..eval.harness import module_specs_from_orm, score_outputs
    from . import ground_truth
    from .ocr_runner import invalid_output_reason, run_ocr_on_samples

    version = db.get(OcrPromptVersion, version_id)
    if not version or not version.composed_prompt:
        return None
    api_def = db.get(ApiDefinition, run.api_definition_id)
    if not api_def:
        return None
    modules = (
        db.query(OcrModule)
        .filter(OcrModule.prompt_version_id == version_id)
        .order_by(OcrModule.order_index)
        .all()
    )
    if not modules:
        return None

    # Confirmed samples + GT (same gate as the run).
    raw = run.sample_document_ids or []
    sample_ids: list[uuid.UUID] = []
    gts: dict[str, dict] = {}
    for sid in raw:
        try:
            suid = uuid.UUID(str(sid))
        except (ValueError, TypeError):
            continue
        gt = ground_truth.build(db, suid)
        if gt:
            sample_ids.append(suid)
            gts[str(suid)] = gt
    if not sample_ids:
        return None

    from app.processors.factory import ProcessorFactory as _PF
    _conf_proc, _conf_model = _PF.resolve_spec(
        api_def.processor_type, api_def.model_name
    )
    # 批次2：降级到 mock 的确认评估不可信 → 返回 None（保守保留已评估最优版）。
    if _PF.is_degraded_to_mock(_conf_proc, api_def.processor_type):
        logger.warning("final-confirmation degraded to mock — skipping (keep best evaluated)")
        return None
    outputs = run_ocr_on_samples(
        db,
        sample_document_ids=sample_ids,
        composed_prompt=version.composed_prompt,
        composed_schema=version.composed_schema,
        processor_spec=_conf_proc,
        model_name=_conf_model,
    )
    # 批次2：传输/解析失败的样本剔除；剩余有效样本太少 → 无法确认（None）。
    invalid = {sid: r for sid, out in outputs.items()
               if (r := invalid_output_reason(out))}
    if invalid:
        logger.warning("final-confirmation: excluding invalid samples %s", invalid)
        outputs = {sid: out for sid, out in outputs.items() if sid not in invalid}
        gts = {sid: gt for sid, gt in gts.items() if sid not in invalid}
    if len(outputs) < min(2, len(sample_ids)):
        return None
    report = score_outputs(module_specs_from_orm(modules), outputs, gts)
    return report.overall_accuracy


def _best_evaluated_version(db: Session, run_id: uuid.UUID) -> tuple[uuid.UUID | None, float]:
    """Monotonic guard (CLAUDE.md §④): pick the version with the HIGHEST
    *evaluated* accuracy across the run, so finalize never activates a version
    we haven't confirmed.

    Each round records `overall_accuracy` for the version it EVALUATED at entry
    (`round.prompt_version_id` = that round's INPUT version): round 1 → starting
    version, round N → v(N-1). So the map covers {starting … v(N-1)} — every
    version EXCEPT the last round's un-evaluated output. Returning the argmax
    guarantees the activated version's accuracy >= the starting version's
    (round-over-round non-decrease), at ZERO extra OCR cost.

    Tradeoff (intentional, safe): the very last round's output is un-evaluated,
    so it is NOT a candidate — we never activate an unconfirmed version. (To
    also capture a final-round gain, `_confirm_version_accuracy` runs one
    confirmation pass in the pipeline.)
    """
    rounds = (
        db.query(OcrOptimizationRound)
        .filter(OcrOptimizationRound.run_id == run_id)
        .order_by(OcrOptimizationRound.round_num)
        .all()
    )
    best_id: uuid.UUID | None = None
    best_acc = -1.0
    for r in rounds:
        if r.overall_accuracy is None or not r.prompt_version_id:
            continue  # 评测无效轮（批次2）不参与
        acc = r.overall_accuracy
        # 批次6 平局带：切换到更晚的版本要求提升超过「半个量化步长」
        # （1/(2·样本数·字段数)）。LLM OCR 单次采样天然抖动，观测分差小于
        # 一个字段一次翻转的一半基本是噪声——持平保早版（更少变更=更稳）。
        band = _tie_band(r)
        if best_id is None or acc > best_acc + band:
            best_acc, best_id = acc, r.prompt_version_id
        elif acc > best_acc:
            best_acc = acc  # 记录观测高值但不切版本（差异在噪声带内）
    return best_id, best_acc


def _tie_band(rnd) -> float:
    """半个量化步长：0.5 / (有效样本数 × 字段数)；信息不足时保守 0.005。"""
    try:
        n_samples = len(rnd.per_sample_accuracy or {}) or None
        if n_samples is None:
            q = rnd.eval_quality or {}
            n_samples = q.get("valid_sample_count") or None
        n_modules = len(rnd.iterations or []) or None
        if n_samples and n_modules:
            return 0.5 / (n_samples * n_modules)
    except Exception:  # noqa: BLE001
        pass
    return 0.005
