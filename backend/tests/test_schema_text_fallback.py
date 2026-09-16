"""qwen 的 schema description 文本回退通道（瘦身计划 R1）。

`response_schema` 是 Gemini 的结构化输出通道，免费且强制；DashScope 视觉模型
不支持它，qwen 收到只能丢弃。模板把字段规则迁进 description 后，这等于
「切到 qwen 备份就丢掉那些规则」——主备两条路按两套规则识别。

本文件锁：qwen 收到 schema 时把 description 渲染成文本附录补进 instruction；
Gemini 不受影响（继续走免费通道，不付这笔 token）。
"""

from __future__ import annotations

import pytest

from app.processors.schema_text import render_schema_descriptions

SCHEMA = {
    "type": "ARRAY",
    "items": {"anyOf": [
        {"type": "OBJECT", "properties": {
            "docType": {"type": "STRING", "description": "票据类型",
                        "enum": ["invoice", "receipt"]},
            "invoiceNumber": {"type": "STRING",
                              "description": "发票号。\n严禁取 SST ID。"},
            "noDesc": {"type": "STRING"},
            "detailOfGoodsOrServices": {"type": "ARRAY", "items": {
                "type": "OBJECT", "properties": {
                    "articleName": {"type": "STRING", "description": "品名本体"},
                }}},
        }},
        {"type": "OBJECT", "properties": {
            "docType": {"type": "STRING", "description": "其他文档",
                        "enum": ["other"]},
            "page": {"type": "ARRAY", "description": "页码列表"},
        }},
    ]},
}


# ── 渲染器 ────────────────────────────────────────────────────────────────────

def test_renders_path_type_enum_and_description():
    out = render_schema_descriptions(SCHEMA)
    assert "`docType`" in out
    assert "仅限：invoice | receipt" in out
    assert "`invoiceNumber`" in out
    assert "严禁取 SST ID" in out


def test_renders_nested_row_fields_with_bracket_path():
    out = render_schema_descriptions(SCHEMA)
    assert "`detailOfGoodsOrServices[].articleName`" in out
    assert "品名本体" in out


def test_field_without_description_or_enum_is_skipped():
    """没有 description 也没有 enum 的字段不占篇幅——结构约束归紧凑字段树。"""
    assert "`noDesc`" not in render_schema_descriptions(SCHEMA)


def test_anyof_duplicate_field_kept_once():
    """docType 在 invoice 支与 other 支都有，附录里只出现一次。"""
    out = render_schema_descriptions(SCHEMA)
    assert out.count("- `docType`") == 1


def test_other_branch_fields_are_included():
    assert "`page`" in render_schema_descriptions(SCHEMA)


@pytest.mark.parametrize("bad", [None, {}, "x", {"type": "OBJECT"},
                                 {"type": "ARRAY", "items": {"type": "OBJECT"}}])
def test_no_descriptions_returns_empty(bad):
    """无 description 可渲染时返回空串——调用方据此跳过，不拼空附录。"""
    assert render_schema_descriptions(bad) == ""


def test_multiline_description_is_indented():
    out = render_schema_descriptions(SCHEMA)
    assert "\n  严禁取 SST ID。" in out, "多行 description 续行需缩进，保持列表从属"


# ── 接线：qwen 拼附录，gemini 不拼 ────────────────────────────────────────────

def test_qwen_appends_appendix_to_instruction(monkeypatch, tmp_path):
    from app.processors import qwen_processor as qp
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "QWEN_API_KEYS", "k1", raising=False)
    captured = {}

    def fake_chat(self, *, model, messages, as_json):
        captured["text"] = messages[0]["content"][-1]["text"]
        return "[]"

    monkeypatch.setattr(qp.QwenProcessor, "_chat", fake_chat)
    monkeypatch.setattr(qp.QwenProcessor, "_image_to_data_url",
                        staticmethod(lambda p: "data:image/png;base64,x"))

    f = tmp_path / "x.png"; f.write_bytes(b"\x89PNG")
    proc = qp.QwenProcessor()

    proc.process_document(str(f), "PROMPT", {"response_schema": SCHEMA})
    assert "PROMPT" in captured["text"]
    assert "字段识别细则" in captured["text"], "qwen 必须收到 description 文本附录"
    assert "严禁取 SST ID" in captured["text"]

    captured.clear()
    proc.process_document(str(f), "PROMPT", None)
    assert captured["text"] == "PROMPT", "没传 schema 时不得凭空加附录"


def test_gemini_does_not_pay_for_appendix(monkeypatch, tmp_path):
    """Gemini 走免费的结构化通道，instruction 保持原样——附录只对
    不支持 response_schema 的 provider 渲染。"""
    from app.processors.gemini_processor import GeminiProcessor

    captured = {}

    class _Resp:
        text = "[]"
        usage_metadata = None

    class _Models:
        def generate_content(self, *, model, contents, config):
            captured["instruction"] = config.system_instruction[0].text
            return _Resp()

    class _Client:
        models = _Models()

    monkeypatch.setattr("app.processors.key_pool._Lease.client",
                        lambda self, factory: _Client())
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "GEMINI_API_KEYS", "k1", raising=False)
    from app.processors import key_pool
    key_pool.reset_pools()

    f = tmp_path / "x.pdf"; f.write_bytes(b"%PDF-1.4")
    GeminiProcessor().process_document(str(f), "PROMPT",
                                       {"response_schema": SCHEMA})
    assert captured["instruction"] == "PROMPT", "Gemini 的 instruction 不得被附录污染"
    key_pool.reset_pools()


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("qwen3-vl-flash", True),
        ("qwen3.5-ocr", True),
        ("qwen3.7-plus", True),
        ("qwen3.8-max", True),
        ("qwen-plus", False),
        ("qwen-max", False),
    ],
)
def test_qwen_vision_name_detection_keeps_new_multimodal_models(name, want):
    from app.processors.qwen_processor import QwenProcessor

    assert QwenProcessor._is_vision_name(name) is want
