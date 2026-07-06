"""L0.1 抓到的存量 bug：黄金评测的 doc key 形态不匹配 → strict 恒全 0。

golden manifest 的 doc_id 是无连字符 hex（"dde67c24f5b4..."），而
run_ocr_on_samples 返回带连字符的 str(UUID)（"dde67c24-f5b4-..."）。
golden_loop 的两条路径（reflect 源 + strict batch）直接拿 hex 作
ground_truths 的 key → score_outputs 里 ocr_outputs.get(doc_id) 恒
None → 全字段 0 分。黄金 CLI 在此修复前从未产出过真实分数
（此前的全 0 被 schema 根形状 bug 与 SSL 失败掩盖）。

golden_review（管理 UI 路径）一直是对的（str(uuid.UUID(did))）——
本测试同时锁住三条路径的键形态一致性。
"""
from __future__ import annotations

import uuid

from app.ocr_optimizer.eval.harness import ModuleSpec, score_outputs


def test_score_outputs_requires_key_alignment():
    """还原 bug 场景：GT 用 hex key、OCR 输出用带连字符 key → 全 0；
    规整后 → 正常得分。这是 golden_loop 修复所依赖的行为契约。"""
    doc = uuid.uuid4()
    spec = ModuleSpec(module_key="inv", json_path="$[*].invoiceNumber",
                      schema_fragment={"type": "string"}, display_name="inv")
    ocr_outputs = {str(doc): [{"invoiceNumber": "INV-1"}]}   # 带连字符（ocr_runner 形态）
    gt = {"invoiceNumber": "INV-1"}

    # 错误形态（修复前 golden_loop 的做法）：hex key → 全 0
    bad = score_outputs([spec], ocr_outputs, {doc.hex: gt}, strict=True)
    assert bad.overall_accuracy == 0.0

    # 正确形态（修复后）：str(UUID) key → 满分
    good = score_outputs([spec], ocr_outputs, {str(doc): gt}, strict=True)
    assert good.overall_accuracy == 1.0


def test_golden_loop_normalizes_hex_doc_ids(monkeypatch):
    """golden_strict_batch 把 manifest 的 hex doc_id 规整为带连字符
    str(UUID) 再喂 evaluate_prompt——用桩验证键形态，零 OCR。"""
    from app.ocr_optimizer.eval import golden_loop

    doc = uuid.uuid4()
    monkeypatch.setattr(golden_loop, "sample_batch", lambda country, **kw: {
        "doc_ids": [doc.hex], "core_fields": ["invoiceNumber"],
        "pool_size": 1, "batch_size": 1,
    })
    monkeypatch.setattr(golden_loop, "load_golden", lambda country: {
        doc.hex: {"gt": {"invoiceNumber": "INV-1"}},
    })
    captured: dict = {}

    def _fake_eval(db, **kw):
        captured.update(kw)
        from app.ocr_optimizer.eval.harness import EvalReport
        return EvalReport(overall_accuracy=1.0, module_scores=[],
                          sample_count=1, module_count=1)

    monkeypatch.setattr(golden_loop, "evaluate_prompt", _fake_eval)

    spec = ModuleSpec(module_key="inv", json_path="$[*].invoiceNumber",
                      schema_fragment={"type": "string"}, display_name="inv")
    golden_loop.golden_strict_batch(
        None, country="MY", modules=[spec],
        composed_prompt="p", composed_schema=None, size=1,
    )
    # GT 键必须是带连字符的 str(UUID)，与 run_ocr_on_samples 输出对齐
    assert list(captured["ground_truths"].keys()) == [str(doc)]
    assert captured["sample_doc_ids"] == [doc]
