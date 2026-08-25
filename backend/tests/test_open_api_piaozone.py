"""开放平台（piaozone 兼容）契约测试。

对齐 `1787497421902_振兴客户请求切片.md` 的真实日志：鉴权格式、入参字段、
响应外壳与分组结构。用 mock processor，零 token 消耗。
"""

from __future__ import annotations

import hashlib
import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import open_api
from app.main import app
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus
from app.models.open_api_client import OpenApiClient
from app.services import open_api_mapper as mapper

CLIENT_ID = "TEST_OpenApiClient01"
SECRET = "test-secret-abc123"
TEMPLATE_ID = 9907


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def seeded(db_session):
    """一个可用的 client + 挂了 external_template_id 的 active API。

    幂等：fixture 内 commit 后 db_session 的 rollback 收不回来，多个测试共用同一个
    测试库，故必须 get-or-create，否则第二个用例就撞 client_id 的唯一约束。
    """
    c = db_session.query(OpenApiClient).filter(
        OpenApiClient.client_id == CLIENT_ID).first()
    if c is None:
        c = OpenApiClient(
            id=uuid.uuid4(), client_id=CLIENT_ID, client_secret=SECRET,
            client_secret_hash=hashlib.sha256(SECRET.encode()).hexdigest(),
            name="Test Client", tenant_id=None, is_active=True,
        )
        db_session.add(c)

    api = db_session.query(ApiDefinition).filter(
        ApiDefinition.external_template_id == TEMPLATE_ID).first()
    if api is None:
        api = ApiDefinition(
            id=uuid.uuid4(), name="test-open-api",
            api_code=f"test-open-{uuid.uuid4().hex[:6]}",
            description="", status=ApiDefinitionStatus.active.value, version=1,
            response_schema={"type": "ARRAY"}, processor_type="mock",
            model_name="mock", external_template_id=TEMPLATE_ID, tenant_id=None,
        )
        db_session.add(api)
    db_session.commit()
    return {"client": c, "api": api}


def _sign(client_id: str, secret: str, ts: str) -> str:
    return hashlib.md5(f"{client_id}{secret}{ts}".encode()).hexdigest()


def _token(client: TestClient, *, client_id=CLIENT_ID, secret=SECRET) -> str:
    ts = str(int(time.time()))
    r = client.post("/base/oauth/token", json={
        "client_id": client_id, "timestamp": ts, "sign": _sign(client_id, secret, ts)})
    assert r.status_code == 200
    return r.json()["access_token"]


# ── 鉴权格式 ─────────────────────────────────────────────────────────────────

def test_oauth_token_success_shape(client, seeded):
    """成功响应字段与线上一致：errcode/description/access_token/token_type/expires_in。"""
    ts = str(int(time.time()))
    r = client.post("/base/oauth/token", json={
        "client_id": CLIENT_ID, "timestamp": ts, "sign": _sign(CLIENT_ID, SECRET, ts)})
    assert r.status_code == 200
    body = r.json()
    assert body["errcode"] == "0000"
    assert body["description"] == "操作成功"
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 129600
    assert len(body["access_token"]) == 32  # 32 位十六进制，与线上一致
    int(body["access_token"], 16)  # 必须是合法十六进制


def test_oauth_token_bad_sign_is_200_with_errcode(client, seeded):
    """签名错也返回 HTTP 200 + 非 0000 errcode（线上调用方按 errcode 分支）。"""
    ts = str(int(time.time()))
    r = client.post("/base/oauth/token", json={
        "client_id": CLIENT_ID, "timestamp": ts, "sign": "deadbeef"})
    assert r.status_code == 200
    assert r.json()["errcode"] != "0000"
    assert "access_token" not in r.json()


def test_oauth_token_unknown_client(client, seeded):
    ts = str(int(time.time()))
    r = client.post("/base/oauth/token", json={
        "client_id": "NOPE", "timestamp": ts, "sign": _sign("NOPE", SECRET, ts)})
    assert r.json()["errcode"] != "0000"


def test_oauth_token_stale_timestamp_rejected(client, seeded):
    """时间戳超窗口拒绝（防重放）。"""
    ts = str(int(time.time()) - 7200)
    r = client.post("/base/oauth/token", json={
        "client_id": CLIENT_ID, "timestamp": ts, "sign": _sign(CLIENT_ID, SECRET, ts)})
    assert r.json()["errcode"] != "0000"


# ── 业务接口：入参与响应结构 ──────────────────────────────────────────────────

def test_analyze_response_matches_log_shape(client, seeded):
    """响应外壳与 header 分组结构逐项对齐日志切片。"""
    token = _token(client)
    r = client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={token}",
        headers={"client-platform": "common"},
        data={"templateId": str(TEMPLATE_ID), "fileHash": "abc123",
              "clientId": CLIENT_ID},
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    # 顶层外壳
    assert set(body) == {"errcode", "description", "data", "traceId", "docPages"}
    assert body["errcode"] == "0000"
    assert body["description"] == "Success"
    assert isinstance(body["data"], list)
    assert len(body["traceId"]) == 16

    if body["data"]:
        entity = body["data"][0]
        assert set(entity) == {"header", "detail"}
        # header 五组，含线上原样拼写的 "bussiness"
        assert set(entity["header"]) == {
            "basic", "billTo", "billFrom", "bussiness", "payment"}
        assert set(entity["detail"]) == {
            "detailOfGoodsOrServices", "detailOfTaxSummary",
            "originalInvoiceReferences"}
        # 字段全集补齐（不省略空字段）
        assert set(entity["header"]["basic"]) == set(mapper.BASIC_FIELDS)
        assert set(entity["header"]["billTo"]) == set(mapper.BILL_TO_FIELDS)
        assert set(entity["header"]["billFrom"]) == set(mapper.BILL_FROM_FIELDS)
        # fileHash 回填到 sourceFileHash
        assert entity["header"]["basic"]["sourceFileHash"] == "abc123"
        # 标量全字符串
        for group in ("basic", "billTo", "billFrom", "bussiness", "payment"):
            for k, v in entity["header"][group].items():
                if k == "page":
                    assert isinstance(v, list) and all(isinstance(p, str) for p in v)
                else:
                    assert isinstance(v, str), f"{group}.{k} 应为字符串，实为 {type(v)}"


def test_analyze_rejects_missing_token(client, seeded):
    r = client.post(
        "/ai/knowledge/nlpService/document/analyze",
        data={"templateId": str(TEMPLATE_ID), "clientId": CLIENT_ID},
        files={"file": ("d.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 200
    assert r.json()["errcode"] != "0000"
    assert r.json()["data"] == []


def test_analyze_unknown_template(client, seeded):
    token = _token(client)
    r = client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={token}",
        data={"templateId": "999999", "clientId": CLIENT_ID},
        files={"file": ("d.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.json()["errcode"] != "0000"


def test_analyze_client_id_mismatch_rejected(client, seeded):
    """拿 A 的 token 冒充 B 提交必须被拒。"""
    token = _token(client)
    r = client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={token}",
        data={"templateId": str(TEMPLATE_ID), "clientId": "SOMEONE_ELSE"},
        files={"file": ("d.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.json()["errcode"] != "0000"


def test_analyze_missing_file(client, seeded):
    token = _token(client)
    r = client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={token}",
        data={"templateId": str(TEMPLATE_ID), "clientId": CLIENT_ID})
    assert r.json()["errcode"] != "0000"


# ── 别名路径（overseaInvoice/extraction）──────────────────────────────────────
# 存量对接方把这条路径写死在客户端里，改不动；服务端挂别名兜住。
# 两条路径必须逐字段等价 —— 若哪天有人只给规范路径加了逻辑，这组测试要报警。

def test_alias_path_returns_same_envelope_as_canonical(client, seeded):
    """别名路径与规范路径响应结构一致（traceId 逐次随机，故排除后比对）。"""
    token = _token(client)

    def _call(path: str) -> dict:
        r = client.post(
            f"{path}?access_token={token}",
            headers={"client-platform": "common"},
            data={"templateId": str(TEMPLATE_ID), "fileHash": "abc123",
                  "clientId": CLIENT_ID},
            files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 fake"),
                            "application/pdf")},
        )
        assert r.status_code == 200
        body = r.json()
        body.pop("traceId")
        return body

    canonical = _call(open_api.ANALYZE_PATH)
    alias = _call(open_api.ANALYZE_PATH_ALIAS)
    assert alias == canonical


def test_alias_path_enforces_the_same_auth(client, seeded):
    """别名不得成为鉴权旁路：缺 token 一样 200 + 非 0000。"""
    r = client.post(
        open_api.ANALYZE_PATH_ALIAS,
        data={"templateId": str(TEMPLATE_ID), "clientId": CLIENT_ID},
        files={"file": ("d.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 200
    assert r.json()["errcode"] != "0000"
    assert r.json()["data"] == []


def test_alias_path_string_is_the_one_the_client_hardcoded(client, seeded):
    """路径字面量本身是对外契约，写死在对方代码里 —— 改一个字符就是线上 404。"""
    assert open_api.ANALYZE_PATH_ALIAS == (
        "/ai/knowledge/nlpService/overseaInvoice/extraction")


# ── 映射器单测（不依赖 HTTP）──────────────────────────────────────────────────

def test_mapper_stringifies_and_fills_all_fields():
    flat = {
        "docType": "invoice", "invoiceNumber": "9311", "totalAmount": 100.24,
        "currency": "MYR", "page": [1],
        "billFromName": "IMPERIO PROPERTY SDN BHD",
        "billFromBusinessRegistrationNumber": "1055647-U",
        "detailOfGoodsOrServices": [
            {"articleName": "FuelSave 95(Pump 2)", "quantity": 50.37,
             "unitPrice": 3.37, "netAmount": 169.75}],
        "detailOfTaxSummary": [],
    }
    out = mapper.map_entity(flat, source_file_hash="e4a6a7f4")
    basic = out["header"]["basic"]
    assert basic["totalAmount"] == "100.24"      # 数字 → 字符串
    assert basic["page"] == ["1"]                # 数组元素也字符串化
    assert basic["sourceFileHash"] == "e4a6a7f4"
    assert basic["invoiceCode"] == ""            # 缺失字段补空串而非省略
    assert out["header"]["billTo"]["billToName"] == ""
    row = out["detail"]["detailOfGoodsOrServices"][0]
    assert set(row) == set(mapper.GOODS_ROW_FIELDS)
    assert row["quantity"] == "50.37" and row["netAmount"] == "169.75"
    assert row["taxRate"] == ""
    assert out["detail"]["originalInvoiceReferences"] == []


def test_mapper_normalises_entity_container_shapes():
    """处理器可能返回数组 / {"entities": []} / 单对象，都要归一。"""
    assert len(mapper.normalise_entities([{"a": 1}, {"b": 2}])) == 2
    assert len(mapper.normalise_entities({"entities": [{"a": 1}]})) == 1
    assert len(mapper.normalise_entities({"data": [{"a": 1}]})) == 1
    assert len(mapper.normalise_entities({"docType": "invoice"})) == 1
    assert mapper.normalise_entities(None) == []


def test_mapper_error_keeps_same_envelope():
    err = mapper.build_error("4004", "invalid access_token", trace_id="abcd1234abcd1234")
    assert set(err) == {"errcode", "description", "data", "traceId", "docPages"}
    assert err["data"] == [] and err["errcode"] == "4004"


# ── 别名兜底（qwen 无 response_schema 硬约束，字段名会漂移）────────────────────

def test_alias_fallback_fills_canonical_keys():
    """模型吐 billFrom/issueDate/subTotal 时，映射到规范键（生产实测过的漂移）。"""
    out = mapper.map_entity({
        "invoiceNumber": "IV-05657",
        "issueDate": "2026-02-12",          # → invoiceDate
        "billFrom": "STE SOLID TIMBER SDN BHD",   # → billFromName
        "billTo": "PP CHIN HIN SDN BHD",    # → billToName
        "billFromAddress": "Lot 123, Selangor",   # → billFromComposite
        "subTotal": 1080.0,                 # → totalNetAmount
    })
    assert out["header"]["basic"]["invoiceDate"] == "2026-02-12"
    assert out["header"]["basic"]["totalNetAmount"] == "1080.0"
    assert out["header"]["billFrom"]["billFromName"] == "STE SOLID TIMBER SDN BHD"
    assert out["header"]["billFrom"]["billFromComposite"] == "Lot 123, Selangor"
    assert out["header"]["billTo"]["billToName"] == "PP CHIN HIN SDN BHD"


def test_alias_never_overrides_canonical_value():
    """规范键已有值时，别名一律不参与——避免把对的覆盖成错的。"""
    out = mapper.map_entity({
        "invoiceDate": "2026-03-31", "issueDate": "1999-01-01",
        "billFromName": "REAL NAME", "billFrom": "WRONG NAME",
    })
    assert out["header"]["basic"]["invoiceDate"] == "2026-03-31"
    assert out["header"]["billFrom"]["billFromName"] == "REAL NAME"


def test_alias_handles_nested_and_ambiguous_shapes():
    """别名值是嵌套对象时取主名；歧义形状（多元素数组）放弃而非猜错。"""
    out = mapper.map_entity({"billFrom": {"name": "ACME SDN BHD", "phone": "123"}})
    assert out["header"]["billFrom"]["billFromName"] == "ACME SDN BHD"

    out2 = mapper.map_entity({"billTo": [{"name": "A"}, {"name": "B"}]})
    assert out2["header"]["billTo"]["billToName"] == ""  # 有歧义 → 留空

    out3 = mapper.map_entity({"billFrom": {"unknownKey": "x"}})
    assert out3["header"]["billFrom"]["billFromName"] == ""  # 取不到主名 → 留空


def test_alias_matching_is_case_and_separator_insensitive():
    """同一规则覆盖 subTotal / subtotalAmount / sub_total_amount 等写法——
    实测同一张票两次调用就吐过两种拼法，逐个字面量加是打地鼠。"""
    for key in ("subTotal", "subtotalAmount", "sub_total_amount", "SubTotalAmount"):
        out = mapper.map_entity({key: 460.0})
        assert out["header"]["basic"]["totalNetAmount"] == "460.0", key


def test_alias_date_is_last_resort_only():
    """`date` 歧义最大，只在没有更确定的日期别名时才兜底。"""
    assert mapper.map_entity({"date": "2026-02-12"})["header"]["basic"]["invoiceDate"] == "2026-02-12"
    # issueDate 更确定 → 优先于 date
    out = mapper.map_entity({"date": "1999-01-01", "issueDate": "2026-02-12"})
    assert out["header"]["basic"]["invoiceDate"] == "2026-02-12"


# ── 超页提示（MAX_PAGES=16，超出部分不识别、不分片）──────────────────────────

def test_page_limit_constant_is_16():
    from app.processors.qwen_processor import MAX_PAGES
    assert MAX_PAGES == 16


def test_truncated_description_format():
    """超页提示语按上限动态生成，errcode 仍为成功。"""
    assert mapper.TRUNCATED_DESC.format(limit=16) == "超过16页的部分，不予以识别"


def test_page_limit_only_applies_to_qwen(monkeypatch):
    """mock/gemini 无此上限 → 不提示截断，避免误导调用方。"""
    from app.api.v1 import open_api
    assert open_api._page_limit("mock") is None


def test_build_response_carries_custom_description():
    """截断时 description 承载提示，errcode 与 data 不受影响。"""
    out = mapper.build_response(
        [{"docType": "invoice"}], trace_id="a" * 16, doc_pages=20,
        description=mapper.TRUNCATED_DESC.format(limit=16))
    assert out["errcode"] == "0000"
    assert out["description"] == "超过16页的部分，不予以识别"
    assert out["docPages"] == 20   # 报原文档实际页数，便于调用方判断截断量
    assert len(out["data"]) == 1


# ── 多票据不得被截断（曾因 _run_processor 里 structured_data[0] 丢掉第2张起）──

def test_multi_invoice_document_returns_all_entities(client, seeded, monkeypatch):
    """模型返回 6 张票据时，接口必须全部返回——历史实现只返回第 1 张。"""
    six = [{"docType": "invoice", "invoiceNumber": f"00{i}0725",
            "totalAmount": 100.0 * i, "currency": "MYR", "page": [i]}
           for i in range(1, 7)]

    from app.services import extract_service as svc

    def fake_run(**kwargs):
        return {}, six, "mock", None

    monkeypatch.setattr(svc, "_run_processor", fake_run)

    token = _token(client)
    r = client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={token}",
        data={"templateId": str(TEMPLATE_ID), "fileHash": "h6", "clientId": CLIENT_ID},
        files={"file": ("six.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")})
    body = r.json()
    assert body["errcode"] == "0000"
    assert len(body["data"]) == 6, f"应返回 6 张，实为 {len(body['data'])}"
    nums = [e["header"]["basic"]["invoiceNumber"] for e in body["data"]]
    assert nums == ["0010725", "0020725", "0030725", "0040725", "0050725", "0060725"]


def test_extract_response_keeps_data_contract_and_adds_entities(db_session, monkeypatch):
    """/api/v1/extract 的 data 仍是「首条 dict」，新增 entities 给全量。"""
    from app.services import extract_service as svc
    two = [{"invoiceNumber": "A1"}, {"invoiceNumber": "A2"}]
    monkeypatch.setattr(svc, "_run_processor", lambda **kw: ({}, two, "mock", None))

    api = db_session.query(ApiDefinition).filter(
        ApiDefinition.external_template_id == TEMPLATE_ID).first()
    res = svc.extract_document(
        db_session, api_code=api.api_code, api_key=None,
        file_bytes=b"%PDF-1.4", filename="x.pdf")
    assert isinstance(res.data, dict) and res.data["invoiceNumber"] == "A1"
    assert len(res.entities) == 2
