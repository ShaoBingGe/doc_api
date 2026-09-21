"""代码审查发现的缺陷的回归测试（2026-09-21）。

这批缺陷的共同点是：**只在 `MYINVOIS_ENABLED=true` 后才发作**，而默认值是
false，所以既有测试与本地开发全都看不到。每条用例对应一个具体失效场景。
"""

from __future__ import annotations

import inspect
import io
import re

import pytest

fitz = pytest.importorskip("fitz")
zxingcpp = pytest.importorskip("zxingcpp")
Image = pytest.importorskip("PIL.Image")

from app.services.myinvois import qr_extractor as qx
from app.services.myinvois.fusion import _page_conflict, fuse_sources
from app.services.myinvois.ubl_mapper import _issue_date_local


# ── 红线⑦：probe 不得在事件循环里跑同步重活 ────────────────────────────────
def test_probe_offloads_blocking_work_to_thread():
    """`probe` 必须把扫描/查验丢线程池。

    二维码扫描是纯 CPU 的整页渲染，`gw.verify()` 是阻塞 HTTP（最长
    MYINVOIS_TIMEOUT_S）。在 async 路由里直接跑会独占事件循环 —— 取 token、
    异步任务轮询、健康检查一并假死（CLAUDE.md 红线⑦记录的线上事故）。
    """
    from app.api.v1 import myinvois as mod

    probe_src = inspect.getsource(mod.probe)
    assert "to_thread" in probe_src, "probe 必须经 anyio.to_thread 执行同步实现"
    for forbidden in ("extract_qr_hits(", "gw.verify(", ".verify("):
        assert forbidden not in probe_src, (
            f"async probe 体内不得直接调用 {forbidden} —— 会阻塞事件循环")
    # 同步实现本身不是协程（否则 to_thread 拿到的是个没跑的 coroutine）
    assert not inspect.iscoroutinefunction(mod._probe_sync)
    # 对照组：health 是普通 def，FastAPI 自动丢线程池
    assert not inspect.iscoroutinefunction(mod.health)


def test_probe_caps_lookups_like_production_path():
    """自检端点不该比生产路径更放纵：同样受 MYINVOIS_MAX_LOOKUPS 限制。"""
    from app.api.v1 import myinvois as mod
    assert "MYINVOIS_MAX_LOOKUPS" in inspect.getsource(mod._probe_sync)


# ── 裁切候选：位图必须与矢量块同一把尺子 ──────────────────────────────────
class _FakeRect:
    def __init__(self, x0, y0, w, h):
        self.x0, self.y0 = x0, y0
        self.width, self.height = w, h


class _FakePage:
    def __init__(self, image_rects, drawing_rects=()):
        self._imgs = image_rects
        self._draws = [{"rect": r} for r in drawing_rects]

    def get_images(self, full=True):
        return [(i,) for i in range(len(self._imgs))]

    def get_image_rects(self, xref):
        return [self._imgs[xref]]

    def get_drawings(self):
        return self._draws


def test_full_page_background_image_is_not_a_crop_candidate():
    """整页背景图不得进裁切候选。

    早先位图分支不做任何过滤，841×599 的整页图也会被收下，于是
    「方形候选门控」形同虚设。随后 600 DPI 裁切 → 7008×4992×3 ≈ **105 MB**
    单个 Pixmap；3 路并发叠在 OCR 渲染上就是红线⑦要防的 OOM。
    """
    page = _FakePage([_FakeRect(0, 0, 841, 599),      # 整页背景
                      _FakeRect(10, 10, 150, 573)])   # 细长侧栏
    assert qx._square_candidates(page) == []


def test_real_qr_sized_bitmap_still_a_candidate():
    """过滤不能误杀：票面二维码尺寸（≈120pt 近正方）必须留下。"""
    page = _FakePage([_FakeRect(400, 60, 120, 120)])
    assert len(qx._square_candidates(page)) == 1


def test_crop_dpi_is_capped_by_rect_size():
    """裁切 DPI 随 rect 增大而自适应下调，保证长边 ≤ _CROP_MAX_PX。"""
    src = inspect.getsource(qx.extract_qr_hits)
    assert "_CROP_MAX_PX" in src, "裁切档必须按 rect 尺寸封顶"
    for side_pt, must_be_capped in ((120, False), (600, True)):
        dpi = int(min(qx._CROP_DPI, 72.0 * qx._CROP_MAX_PX / side_pt))
        px = side_pt / 72.0 * dpi
        assert px <= qx._CROP_MAX_PX + 1, f"{side_pt}pt 裁切后 {px:.0f}px 超限"
        if must_be_capped:
            assert dpi < qx._CROP_DPI, "大 rect 必须降 DPI"
        else:
            assert dpi == qx._CROP_DPI, "真二维码尺寸不该被降 DPI"


# ── 扫描段刹车 ────────────────────────────────────────────────────────────
def _qr_png(text: str, scale: int = 8) -> bytes:
    bc = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    mv = memoryview(zxingcpp.write_barcode_to_image(bc, scale=scale))
    h, w = mv.shape
    buf = io.BytesIO()
    Image.frombytes("L", (w, h), bytes(mv)).save(buf, "PNG")
    return buf.getvalue()


_URL = ("https://myinvois.hasil.gov.my/R0JMAZWCTPZQ3X8BJZ9XGKNK10"
        "/share/XWKBWBW1PVC54FYPJZ9XGKNK10CTaHWk1775550506")


def _pdf(pages: int, qr_on_page: int) -> bytes:
    doc = fitz.open()
    for i in range(1, pages + 1):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((60, 60), f"page {i}")
        if i == qr_on_page:
            pg.insert_image(fitz.Rect(400, 60, 520, 180), stream=_qr_png(_URL))
    out = doc.tobytes()
    doc.close()
    return out


def test_max_pages_stops_scanning():
    """超出 max_pages 的页不再扫描 —— 否则百页 PDF 会占死线程池。"""
    pdf = _pdf(pages=8, qr_on_page=7)
    assert qx.extract_qr_hits(pdf, max_pages=None) != [], "不设限时本应扫到第 7 页"
    assert qx.extract_qr_hits(pdf, max_pages=3) == [], "max_pages=3 不该扫到第 7 页"


def test_scan_budget_stops_scanning():
    """时间预算用尽即停，按已扫页返回部分结果（优于无结果）。"""
    pdf = _pdf(pages=8, qr_on_page=7)
    assert qx.extract_qr_hits(pdf, budget_s=0.0) == [], "预算为 0 应立刻停"


def test_scan_brakes_are_wired_from_settings():
    """生产路径必须真的把刹车传下去（参数存在却不传等于没有）。"""
    from app.services.myinvois import enricher
    src = inspect.getsource(enricher.lookup_myinvois)
    assert "max_pages=" in src and "budget_s=" in src, (
        "lookup_myinvois 必须把 max_pages / budget_s 传给 extract_qr_hits")


# ── 日期：UBL 的 IssueDate/IssueTime 是 UTC ───────────────────────────────
@pytest.mark.parametrize("date_s,time_s,expect,why", [
    ("2026-09-20", "14:40:29Z", "2026-09-20", "UTC 下午 → 同日 MYT"),
    ("2026-09-20", "15:59:59Z", "2026-09-20", "临界前"),
    ("2026-09-20", "16:00:00Z", "2026-09-21", "临界：MYT 次日 00:00"),
    ("2026-09-20", "23:30:00Z", "2026-09-21", "跨日"),
    ("2026-09-20", "09:00:00+00:00", "2026-09-20", "+00:00 亦为 UTC"),
    ("2026-09-20", "09:00:00", "2026-09-20", "无时区标记 → 保守不动"),
    ("2026-09-20", None, "2026-09-20", "无 IssueTime → 保守不动"),
    ("2026-09-20", "", "2026-09-20", "空 IssueTime → 保守不动"),
])
def test_issue_date_converted_to_malaysia_local(date_s, time_s, expect, why):
    """MyInvois 规定 IssueDate/IssueTime 按 UTC 提交（IssueTime 带 Z）。

    直接取 IssueDate 等于拿 UTC 日期当票面日期：00:00–08:00 MYT 开具的票会
    早一天。又因「税局恒覆盖 OCR」，这会把 OCR 本来读对的日期覆盖成错的，
    月末还跨账期。没有 UTC 标记时一律不动 —— 宁可不改，不能再推后一天。
    """
    assert _issue_date_local(date_s, time_s) == expect, why


def test_map_document_actually_applies_the_conversion():
    """光有换算函数不够 —— map_document 必须真的调它（否则等于没改）。"""
    import json
    import pathlib as _pl

    from app.services.myinvois.ubl_mapper import map_document

    fx = _pl.Path(__file__).parent / "fixtures" / "myinvois" / "preprod_raw_myr.json"
    raw = json.loads(fx.read_text(encoding="utf-8"))
    assert "<cbc:IssueTime>07:47:11Z</cbc:IssueTime>" in raw["document"]

    # 原样：07:47Z = 15:47 MYT，同日
    assert map_document(raw)["invoiceDate"] == "2026-09-17"

    # 改成 18:30Z = 次日 02:30 MYT —— 票面写的是 18 号，UTC 日期却是 17 号
    late = {**raw, "document": raw["document"].replace(
        "<cbc:IssueTime>07:47:11Z</cbc:IssueTime>",
        "<cbc:IssueTime>18:30:00Z</cbc:IssueTime>")}
    assert map_document(late)["invoiceDate"] == "2026-09-18", (
        "IssueDate 是 UTC 日期，必须折算到马来本地，否则比票面早一天")


def test_original_invoice_date_also_converted():
    """贷项通知单引用的原发票日期同样是对外输出的票据日期，必须一并折算。"""
    import json
    import pathlib as _pl

    from app.services.myinvois.ubl_mapper import map_document

    fx = _pl.Path(__file__).parent / "fixtures" / "myinvois" / "preprod_raw_myr.json"
    raw = json.loads(fx.read_text(encoding="utf-8"))
    ref = ("<cac:BillingReference><cac:InvoiceDocumentReference>"
           "<cbc:ID>INV-OLD-001</cbc:ID>"
           "<cbc:IssueDate>2026-09-17</cbc:IssueDate>"
           "<cbc:IssueTime>18:30:00Z</cbc:IssueTime>"
           "</cac:InvoiceDocumentReference></cac:BillingReference>")
    doc = raw["document"].replace("</cbc:InvoiceTypeCode>",
                                  "</cbc:InvoiceTypeCode>" + ref, 1)
    rows = map_document({**raw, "document": doc})["originalInvoiceReferences"]
    # 18:30Z = 次日 02:30 MYT → 票面日期是 18 号，UTC 日期却是 17 号
    assert rows[0]["originalInvoiceDate"] == "2026-09-18"
    assert rows[0]["originalInvoiceNumber"] == "INV-OLD-001"


def test_every_output_date_goes_through_local_conversion():
    """守卫：ubl_mapper 里**所有**写对外日期字段的地方都必须走 _issue_date_local。

    `_norm_date` 只裁时间后缀、不做时区折算 —— 直接拿它写输出字段，等于把
    UTC 日期当票面日期对外发布。新增日期字段时本用例会拦住遗漏。
    """
    import re as _re

    from app.services.myinvois import ubl_mapper

    src = inspect.getsource(ubl_mapper)
    bad = _re.findall(
        r"^\s*(?:out|row)\[[^\]]*[Dd]ate[^\]]*\]\s*=\s*_norm_date\(",
        src, _re.MULTILINE)
    assert not bad, f"这些日期字段绕过了时区折算：{bad}"


def test_issue_date_tolerates_garbage():
    """脏数据不得抛异常（§五 降级而非 500）。"""
    for d, t in (("not-a-date", "10:00:00Z"), ("2026-13-45", "10:00:00Z"),
                 ("2026-09-20", "99:99:99Z")):
        assert isinstance(_issue_date_local(d, t), str)


# ── 配对：子串匹配不得抢票 ────────────────────────────────────────────────
def _ref(page=1, uuid="U" * 26):
    from app.services.myinvois.link_extractor import MyInvoisRef
    return MyInvoisRef(uuid=uuid, share_token="", page=page,
                       source="annotation", url="")


def test_substring_invoice_number_does_not_steal_another_invoice():
    """`INV-1` 不得抢走 `INV-12` 的税局数据。

    旧写法「取首个子串命中」在多票 PDF 下会把整张票覆盖成另一张票 ——
    在「税局恒覆盖」前提下，这是无界的数据污染。
    """
    ents = [{"invoiceNumber": "INV-1", "docType": "invoice", "page": [9]},
            {"invoiceNumber": "INV-12", "docType": "invoice", "page": [9]}]
    lhdn = {"invoiceNumber": "INV-12", "billFromName": "税局登记名"}
    fused, _ = fuse_sources(ents, [("lhdn", [(_ref(page=7), lhdn)])])
    assert fused[1].get("billFromName") == "税局登记名", "应配到 INV-12"
    assert "billFromName" not in fused[0], "INV-1 不该被 INV-12 的数据覆盖"


def test_ambiguous_substring_is_skipped_not_guessed():
    """子串候选不唯一时不做猜测，回退纯 OCR（有明确定义的安全态）。"""
    ents = [{"invoiceNumber": "INV-1", "docType": "invoice", "page": [9]},
            {"invoiceNumber": "INV-123", "docType": "invoice", "page": [9]}]
    lhdn = {"invoiceNumber": "INV-1234", "billFromName": "税局登记名"}
    fused, rep = fuse_sources(ents, [("lhdn", [(_ref(page=7), lhdn)])])
    assert all("billFromName" not in e for e in fused)
    assert rep.as_dict()["skipped"], "应记录跳过原因，而非静默"


# ── 单票单码直配：页码冲突时不绑 ──────────────────────────────────────────
def test_single_pair_binds_when_pages_agree():
    """常规单票单码：直配，且不拿发票号做模糊比对（OCR 读错号正是要纠正的场景）。"""
    ents = [{"invoiceNumber": "OCR读错的号", "docType": "invoice", "page": [1]}]
    lhdn = {"invoiceNumber": "INV-REAL-001", "billFromName": "税局登记名"}
    fused, _ = fuse_sources(ents, [("lhdn", [(_ref(page=1), lhdn)])])
    assert fused[0]["invoiceNumber"] == "INV-REAL-001", "税局应纠正 OCR 读错的号"


def test_single_pair_not_bound_when_qr_page_outside_entity():
    """附件页夹了另一张带码的票 → 页码是硬证据，不绑。"""
    ents = [{"invoiceNumber": "INV-A", "docType": "invoice", "page": [1]}]
    lhdn = {"invoiceNumber": "INV-B", "billFromName": "另一张票的供应商"}
    fused, rep = fuse_sources(ents, [("lhdn", [(_ref(page=3), lhdn)])])
    assert "billFromName" not in fused[0], "第 3 页的码不该覆盖只占第 1 页的票"
    assert rep.as_dict()["skipped"]


@pytest.mark.parametrize("page,pages,conflict", [
    (1, [1], False), (3, [1], True), (3, [1, 2, 3], False),
    (1, None, False),        # 实体无页码 → 不阻断
    (None, [1], False),      # 引用无页码 → 不阻断
    (1, 1, False),           # 标量页码
    (2, "第1页", False),      # 脏数据 → 不阻断
])
def test_page_conflict_only_on_positive_evidence(page, pages, conflict):
    """只在两侧页码都在且不相交时判冲突；缺信息一律不阻断。"""
    assert _page_conflict(_ref(page=page), {"page": pages}) is conflict


# ── 注入的 httpx client 不得被覆盖 ────────────────────────────────────────
@pytest.fixture
def enabled(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "MYINVOIS_ENABLED", True, raising=False)


def test_injected_http_client_is_not_clobbered(enabled):
    """`MyInvoisGateway(client=...)` 注入的 client 必须存活。

    早先 `gw._http = http` 无条件覆盖，会让 MockTransport 真的发出网络请求；
    现有测试只因 _StubGateway 整个替掉 verify() 才没暴露。
    """
    import httpx

    from app.services.myinvois.enricher import lookup_myinvois
    from app.services.myinvois.gateway_client import MyInvoisGateway

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"errorcode": "500", "message": "stub"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gw = MyInvoisGateway(base_url="http://stub.invalid", client_id="cid",
                         client_secret="sec", company_id="ouno", client=client)
    lookup_myinvois(_pdf(pages=1, qr_on_page=1), gateway=gw)
    assert seen, "注入的 MockTransport 必须被真正使用（否则就是发了真请求）"
    assert all("stub.invalid" in u for u in seen)
    assert gw._http is client, "调用方注入的 client 不该被置空"
    client.close()


# ── token 按凭证分键 ──────────────────────────────────────────────────────
def test_token_cache_is_keyed_by_credentials():
    """换一组凭证（SIT ↔ 生产）不得串 token。"""
    from app.services.myinvois.gateway_client import MyInvoisGateway, _Token

    MyInvoisGateway.reset_token()
    a = MyInvoisGateway(base_url="http://a", client_id="A",
                        client_secret="s", company_id="1")
    b = MyInvoisGateway(base_url="http://b", client_id="B",
                        client_secret="s", company_id="2")
    assert a._cred_key != b._cred_key
    MyInvoisGateway._tokens[a._cred_key] = _Token("token-A", 2 ** 40)
    assert a.token() == "token-A"

    # B 必须拿不到 A 的 token。挡住真实铸币，避免测试发网络请求。
    minted: list[str] = []
    b._mint_token = lambda: (minted.append("mint"), "token-B")[1]
    assert b.token() == "token-B", "B 不该复用 A 的 token"
    assert minted, "B 应当自己去换 token，而不是读到别人的缓存"
    MyInvoisGateway.reset_token()


# ── CMYK 不是 RGBA ────────────────────────────────────────────────────────
class _FakePix:
    def __init__(self, n, alpha):
        self.n, self.alpha = n, alpha


@pytest.mark.parametrize("n,alpha,expect", [
    (1, 0, "Lum"), (3, 0, "RGB"), (4, 1, "RGBA"),
    (4, 0, None),      # CMYK —— 不是 RGBA
    (2, 1, None),      # 灰度+alpha
])
def test_fmt_for_distinguishes_cmyk_from_rgba(n, alpha, expect):
    """`n == 4` 既可能是 RGBA 也可能是 CMYK，必须看 alpha。

    早先只按通道数判定，CMYK 位图被当 RGBA 硬解，注释声称的「CMYK→RGB」
    兜底永远走不到，内嵌位图这条最便宜的快路径在 CMYK 票上必然落空。
    """
    got = qx._fmt_for(_FakePix(n, alpha))
    if expect is None:
        assert got is None
    else:
        assert got == getattr(zxingcpp.ImageFormat, expect)


# ── 审计：消融报告必须落库，且 structured_data 保持纯 OCR ─────────────────
def test_audit_records_fusion_report_and_keeps_raw_ocr():
    """审计要能复盘「税局改了什么」，但 structured_data 必须仍是模型原样输出。

    structured_data 要喂给优化器的评测 harness —— 存成消融后的值会让优化器
    把税局的纠正当成模型自己的功劳，prompt 永远学不到那些字段，污染整条
    自迭代回路。
    """
    from app.services import extract_service as es

    src = inspect.getsource(es.extract_document)
    i_fuse = src.index("fuse_lookup(entities, lookup)")
    i_audit = src.index("_persist_audit(")
    assert i_audit > i_fuse, "审计必须落在消融之后，否则消融不可复盘"
    assert "myinvois_report=myinvois_meta" in src, "消融报告必须随审计落库"
    assert re.search(r"structured_data=structured_data\b", src), (
        "structured_data 必须仍是纯 OCR，不得换成消融后的实体")
