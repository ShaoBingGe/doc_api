"""批次2 回归：降级/失败样本必须从质量决策中剔除。

历史问题：
  - 单样本 OCR 传输失败（限流/超时/截断）按 0 分计入聚合 —— 3 样本下一次
    网络抖动 = 整轮分数掉 1/3，足以让单调守护误杀真实更优的版本，并让
    optimizer 对着「OCR error」假 diff 改 prompt；
  - resolve_spec 静默降级到 mock 后，mock 固定 fixture 照常参与真实打分，
    整个 job「成功完成 3 轮」而 prompt 被随机劣化。
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.ocr_optimizer.service.ocr_runner import invalid_output_reason
from app.processors.factory import ProcessorFactory


@pytest.fixture()
def settings_sandbox(monkeypatch):
    s = get_settings()
    yield s, monkeypatch


# ── invalid_output_reason ────────────────────────────────────────────────────

def test_transport_failures_are_invalid():
    assert invalid_output_reason({"_error": "timeout"}) is not None
    assert invalid_output_reason({"_raw": "not json ..."}) is not None
    assert invalid_output_reason({}) is not None       # 空响应
    assert invalid_output_reason(None) is not None
    assert invalid_output_reason("bare string") is not None
    assert invalid_output_reason(123) is not None


def test_parseable_json_containers_are_valid():
    assert invalid_output_reason({"invoiceNumber": "INV-1"}) is None
    assert invalid_output_reason([{"invoiceNumber": "INV-1"}]) is None
    # 空数组 = 模型判定「无记录」，是真实提取结果，不是传输失败
    assert invalid_output_reason([]) is None


# ── is_degraded_to_mock ──────────────────────────────────────────────────────

def test_intentional_mock_is_not_degraded(settings_sandbox):
    s, mp = settings_sandbox
    mp.setattr(s, "DEFAULT_PROCESSOR", "mock", raising=False)
    # 行偏好显式 mock
    assert ProcessorFactory.is_degraded_to_mock("mock", "mock") is False
    # DEFAULT_PROCESSOR 显式 mock（开发/测试）
    assert ProcessorFactory.is_degraded_to_mock("mock", "gemini") is False
    # 完全无偏好
    mp.setattr(s, "DEFAULT_PROCESSOR", "", raising=False)
    assert ProcessorFactory.is_degraded_to_mock("mock", None) is False


def test_real_provider_wanted_but_mock_resolved_is_degraded(settings_sandbox):
    s, mp = settings_sandbox
    mp.setattr(s, "DEFAULT_PROCESSOR", "qwen", raising=False)
    # 行偏好 gemini、默认 qwen，双双不可用 → 落 mock = 降级
    assert ProcessorFactory.is_degraded_to_mock("mock", "gemini") is True
    # 无行偏好但默认想要 qwen → 落 mock = 降级
    assert ProcessorFactory.is_degraded_to_mock("mock", None) is True


def test_resolved_real_provider_is_never_degraded(settings_sandbox):
    s, mp = settings_sandbox
    mp.setattr(s, "DEFAULT_PROCESSOR", "qwen", raising=False)
    assert ProcessorFactory.is_degraded_to_mock("qwen", "gemini") is False
    assert ProcessorFactory.is_degraded_to_mock("gemini", "gemini") is False


# ── 无效轮不参与单调守护 ─────────────────────────────────────────────────────

def test_invalid_round_excluded_from_best_evaluated_version(db_session):
    """overall_accuracy=None 的轮（评测无效）绝不能被单调守护选中。"""
    from app.ocr_optimizer.models import (
        OcrOptimizationRun, OcrOptimizationRound, RunStatus, RoundPhase,
    )
    from app.ocr_optimizer.service.customer_iteration import _best_evaluated_version

    v1, v2 = uuid.uuid4(), uuid.uuid4()
    run = OcrOptimizationRun(
        id=uuid.uuid4(), api_definition_id=uuid.uuid4(),
        starting_version_id=v1, status=RunStatus.running.value,
        sample_document_ids=[], llm_provider="mock|",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(OcrOptimizationRound(
        id=uuid.uuid4(), run_id=run.id, round_num=1,
        prompt_version_id=v1, overall_accuracy=0.8,
        phase=RoundPhase.completed.value,
    ))
    # 第 2 轮评测无效（降级/样本全失败）：分数为 None + eval_quality.invalid
    db_session.add(OcrOptimizationRound(
        id=uuid.uuid4(), run_id=run.id, round_num=2,
        prompt_version_id=v2, overall_accuracy=None,
        eval_quality={"invalid": True, "degraded_to_mock": True},
        phase=RoundPhase.failed.value,
    ))
    db_session.commit()

    best_id, best_acc = _best_evaluated_version(db_session, run.id)
    assert best_id == v1
    assert best_acc == 0.8


# ── _run_one_round 集成：样本失败剔除 / 全失败轮次无效 ───────────────────────

@pytest.fixture()
def mock_env(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "DEFAULT_PROCESSOR", "mock", raising=False)
    monkeypatch.setattr(s, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(s, "LLM_FALLBACK_CHAIN", "mock|", raising=False)
    yield s


def _setup_api_with_samples(db, *, n_samples: int = 3):
    from app.models.annotation import Annotation
    from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
    from app.models.document import Document, DocumentStatus
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus

    api = ApiDefinition(
        id=uuid.uuid4(), name="evalvalidity", api_code=f"ev-{uuid.uuid4().hex[:8]}",
        status=ApiDefinitionStatus.active,
        processor_type="mock", model_name=None,
        config={"sample_document_ids": []},
    )
    db.add(api)
    ver = OcrPromptVersion(
        id=uuid.uuid4(), api_definition_id=api.id, version="1",
        status=PromptVersionStatus.active.value,
        composed_prompt="GLOBAL_PREAMBLE\nextract",
        composed_schema={"type": "object", "properties": {"invoiceNumber": {"type": "string"}}},
    )
    db.add(ver)
    db.add(OcrModule(
        id=uuid.uuid4(), prompt_version_id=ver.id, module_key="invoice_number",
        display_name="发票号", json_path="$.invoiceNumber",
        ocr_prompt="find invoice number", description="invoice no",
        schema_fragment={"type": "string"}, order_index=1, status="active",
    ))
    sample_ids = []
    for i in range(n_samples):
        doc = Document(
            id=uuid.uuid4(), filename=f"ev{i}.pdf", file_type="pdf", file_size=100,
            status=DocumentStatus.completed, storage_path=f"/tmp/ev-{i}.pdf",
            api_definition_id=api.id,
        )
        db.add(doc)
        db.add(Annotation(
            id=uuid.uuid4(), document_id=doc.id, field_name="invoiceNumber",
            field_value=f"INV-{i:03d}", field_type="string",
            source="manual", is_corrected=True,
        ))
        sample_ids.append(str(doc.id))
    api.config = {"sample_document_ids": sample_ids}
    db.commit()
    return api, ver, sample_ids


def test_all_samples_failed_marks_round_invalid(db_session, mock_env, monkeypatch):
    """全部样本 OCR 传输失败 → 轮次 invalid：无假 0 分、不触发优化、
    next_version 指回当前版本（合法「无变化」信号）。"""
    from app.ocr_optimizer.service import ocr_runner, run_orchestrator
    from app.ocr_optimizer.models import RoundPhase, RunStatus

    api, ver, sample_ids = _setup_api_with_samples(db_session)
    monkeypatch.setattr(
        ocr_runner, "run_ocr_on_samples",
        lambda db, *, sample_document_ids, **kw: {
            str(sid): {"_error": "429 rate limited"} for sid in sample_document_ids
        },
    )
    run = run_orchestrator.start_optimization(
        db_session, api.id, max_rounds=3, enable_meta=False,
    )
    assert run.status == RunStatus.paused_for_review.value  # job 不挂
    rnd = run.rounds[0]
    assert rnd.phase == RoundPhase.failed.value
    assert rnd.overall_accuracy is None          # 不可信分数绝不落库
    assert rnd.next_version_id == ver.id          # 无变化信号
    q = rnd.eval_quality or {}
    assert q.get("invalid") is True
    assert q.get("valid_sample_count") == 0
    assert len(q.get("excluded_samples") or {}) == 3
    # 无效轮不产生任何模块迭代打分（不喂 optimizer 假 diff）
    assert rnd.iterations == []


def test_partial_failure_excludes_bad_sample_only(db_session, mock_env, monkeypatch):
    """3 样本中 1 个失败 → 只剔除坏样本：分数在 2 个有效样本上算，
    per_sample_results 里没有「OCR error」的 0 分假记录。"""
    from app.ocr_optimizer.service import ocr_runner, run_orchestrator
    from app.ocr_optimizer.models import RoundPhase

    api, ver, sample_ids = _setup_api_with_samples(db_session)
    bad = sample_ids[0]

    def _fake_ocr(db, *, sample_document_ids, **kw):
        out = {}
        for sid in sample_document_ids:
            s = str(sid)
            out[s] = ({"_error": "timeout"} if s == bad
                      else {"invoiceNumber": "INV-000"})
        return out

    monkeypatch.setattr(ocr_runner, "run_ocr_on_samples", _fake_ocr)
    run = run_orchestrator.start_optimization(
        db_session, api.id, max_rounds=3, enable_meta=False,
    )
    rnd = run.rounds[0]
    assert rnd.phase != RoundPhase.failed.value
    assert rnd.overall_accuracy is not None
    q = rnd.eval_quality or {}
    assert q.get("invalid") is False
    assert q.get("valid_sample_count") == 2
    assert list((q.get("excluded_samples") or {}).keys()) == [bad]
    # 剔除样本不产生 0 分记录，也不出现在 per_sample_accuracy
    assert bad not in (rnd.per_sample_accuracy or {})
    for it in rnd.iterations:
        for p in it.per_sample_results:
            assert p["sample_doc_id"] != bad
            assert "OCR error" not in (p.get("diff_detail") or "")
