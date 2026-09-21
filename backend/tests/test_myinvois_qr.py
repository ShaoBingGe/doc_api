"""二维码识别模块：自包含合成用例 + 真实语料用例。

合成用例用 zxing-cpp 现场生成二维码再贴进 PDF，不依赖任何含 PII 的语料，
CI 上可稳定复跑；真实语料用例负责守住"矢量码 / 内嵌位图 / 多票据"这些
只有真票才有的形态，语料不在时自动跳过。
"""

from __future__ import annotations

import io
import pathlib

import pytest

fitz = pytest.importorskip("fitz")
zxingcpp = pytest.importorskip("zxingcpp")
Image = pytest.importorskip("PIL.Image")

from app.services.myinvois.qr_extractor import QrHit, extract_qr_hits

CORPUS = pathlib.Path(__file__).parent.parent.parent / "MY-test"

VALID_URL = ("https://myinvois.hasil.gov.my/R0JMAZWCTPZQ3X8BJZ9XGKNK10"
             "/share/XWKBWBW1PVC54FYPJZ9XGKNK10CTaHWk1775550506")


def _qr_png(text: str, scale: int = 8) -> bytes:
    """生成一张二维码 PNG（8-bit 灰度，尺寸由 scale 决定）。"""
    bc = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    img = zxingcpp.write_barcode_to_image(bc, scale=scale)
    mv = memoryview(img)
    h, w = mv.shape
    buf = io.BytesIO()
    Image.frombytes("L", (w, h), bytes(mv)).save(buf, "PNG")
    return buf.getvalue()


def _pdf_with(texts: list[str | None], per_page: int = 1) -> bytes:
    """造一个 PDF：每页贴一个二维码（None = 该页不贴，模拟无码页）。"""
    doc = fitz.open()
    for t in texts:
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 60), "TAX INVOICE / 测试票面")
        if t is not None:
            page.insert_image(fitz.Rect(400, 60, 520, 180), stream=_qr_png(t))
    out = doc.tobytes()
    doc.close()
    return out


# ── 合成用例 ────────────────────────────────────────────────────────────────
def test_decodes_valid_myinvois_qr():
    hits = extract_qr_hits(_pdf_with([VALID_URL]))
    assert len(hits) == 1
    h = hits[0]
    assert h.uuid == "R0JMAZWCTPZQ3X8BJZ9XGKNK10"
    assert h.long_id == "XWKBWBW1PVC54FYPJZ9XGKNK10CTaHWk1775550506"
    assert h.page == 1
    assert h.method in ("embedded", "render150", "render250", "render300", "crop600")


def test_rejects_non_myinvois_qr_payment_code():
    """实测语料里真出现过：票面贴的是 DuitNow/Grab 支付码（EMVCo 格式）。

    见码就收会把支付码当成税局引用 —— 必须靠域名白名单挡掉。
    """
    pay = "00020101021126530008com.grab0132859f364c99345f0a8d4a1f2b3c4d5e6f5204"
    assert extract_qr_hits(_pdf_with([pay])) == []


def test_rejects_internal_invoice_qr():
    """另一种真实误报源：厂商自编的内部码。"""
    assert extract_qr_hits(_pdf_with(["INV.MAL202626FC004123"])) == []


def test_rejects_malformed_url_with_empty_uuid():
    """沙箱 088 真实形态：报送失败的票，二维码里 uuid 与 token 都是空的。

    朴素实现（只匹配域名）会把它当成命中；uuid 为空拿去查税局必然失败。
    """
    assert extract_qr_hits(_pdf_with(["https://preprod.myinvois.hasil.gov.my//share/"])) == []


def test_accepts_preprod_dot_and_dash_hosts():
    """沙箱用 preprod.myinvois（点），文档/接口用 preprod-api（横线），都要认。"""
    for host in ("preprod.myinvois.hasil.gov.my", "preprod-myinvois.hasil.gov.my"):
        url = f"https://{host}/R4M6XHDBQFKEK46WS10C5Q2M10/share/NTA330J0GBSEPSNST10C5Q2M10g5Q7sU1789631266"
        hits = extract_qr_hits(_pdf_with([url]))
        assert len(hits) == 1, host
        assert hits[0].uuid == "R4M6XHDBQFKEK46WS10C5Q2M10"


def test_uuid_only_url_without_share_token():
    """少数票只有 uuid 没有 share token —— 认证取数只需 uuid，不能丢。"""
    hits = extract_qr_hits(_pdf_with(["https://myinvois.hasil.gov.my/2GRZYF6XRD34DGKR5GPFNDRK10"]))
    assert len(hits) == 1
    assert hits[0].uuid == "2GRZYF6XRD34DGKR5GPFNDRK10"
    assert hits[0].long_id == ""


def test_multi_page_multi_invoice_keeps_all_and_tags_pages():
    """一份 PDF 多张票（实测最多 12 张），每张 uuid 不同，页码要对得上。"""
    u = ["https://myinvois.hasil.gov.my/AAAAAAAAAAAAAAAAAAAAAAAAA1/share/" + "T" * 30,
         None,
         "https://myinvois.hasil.gov.my/BBBBBBBBBBBBBBBBBBBBBBBBB2/share/" + "U" * 30]
    hits = extract_qr_hits(_pdf_with(u))
    assert len(hits) == 2
    assert {h.uuid for h in hits} == {"AAAAAAAAAAAAAAAAAAAAAAAAA1", "BBBBBBBBBBBBBBBBBBBBBBBBB2"}
    assert sorted(h.page for h in hits) == [1, 3]


def test_dedupes_same_qr_repeated_across_pages():
    hits = extract_qr_hits(_pdf_with([VALID_URL, VALID_URL]))
    assert len(hits) == 1


def test_page_without_qr_yields_nothing():
    assert extract_qr_hits(_pdf_with([None])) == []


@pytest.mark.parametrize("blob", [b"", b"not a pdf", b"%PDF-broken"])
def test_never_raises_on_garbage(blob):
    assert extract_qr_hits(blob) == []


def test_max_pages_limits_work():
    pdf = _pdf_with([None, None, VALID_URL])
    assert extract_qr_hits(pdf, max_pages=2) == []
    assert len(extract_qr_hits(pdf)) == 1


def test_as_dict_masks_share_token():
    h = QrHit(uuid="U" * 26, long_id="S" * 40, page=1, url="x", method="embedded")
    d = h.as_dict()
    assert d["long_id_prefix"] == "S" * 8 and d["has_long_id"] is True
    assert "S" * 40 not in str(d), "分享密钥不得整体出现在回执/日志里"


# ── 真实语料用例（语料不在则跳过）──────────────────────────────────────────
@pytest.mark.parametrize("name,expect_method", [
    # 矢量绘制的二维码：取内嵌位图拿不到，必须整页渲染
    ("INV_WMSB-26_04_0138_20260407_163842.pdf", "render"),
    # 内嵌位图的二维码：走最便宜那条路
    ("IN-SAAC-51-PP CHIN HIN SDN BHD  IPOH BRANCH-MYR-20260326_20260326_061700.pdf", "embedded"),
])
def test_real_invoice_shapes(name, expect_method):
    p = CORPUS / name
    if not p.exists():
        pytest.skip("语料不在（MY-test 未入库）")
    hits = extract_qr_hits(p.read_bytes())
    assert len(hits) >= 1
    h = hits[0]
    assert len(h.uuid) == 26
    assert h.long_id, "真实票面二维码应同时带 share token(longId)"
    assert h.method.startswith(expect_method)


def test_real_multi_invoice_pdf():
    p = CORPUS / "001 E-Invoice -New_20251022_120841.pdf"
    if not p.exists():
        pytest.skip("语料不在")
    hits = extract_qr_hits(p.read_bytes())
    assert len(hits) == 7, "该样本含 7 张票"
    assert len({h.uuid for h in hits}) == 7
    assert all(h.long_id for h in hits)


# ── 平台相关的悬垂缓冲区守卫 ────────────────────────────────────────────────
def test_decode_uses_zero_copy_memoryview_not_temporary_bytes():
    """`_decode_pixmap` 必须用 `samples_mv` 且绑到局部变量 —— 源码级守卫。

    为什么不是行为测试：这条 bug 在 macOS 上**不复现**（分配器恰好没复用
    被释放的页），只有 Linux 才现形，所以任何"解一张码看看"的用例都拦不住它。
    2026-09-21 部署腾讯云（Rocky 9 / cp312）实测：
      · `zxingcpp.ImageView(pix.samples, ...)` —— pytest 下**段错误**，
        独立脚本下**静默解出 []**（更危险：税局查验对每张票悄悄失效）；
      · 绑到局部变量的 `pix.samples_mv` —— 正常解出。
    根因：ImageView 零拷贝只存裸指针不持 Python 引用，而 `pix.samples`
    每次访问新建临时 bytes，构造函数一返回即被回收。
    注意 `except Exception` **拦不住段错误**，线上会直接打死 uvicorn worker。
    """
    import inspect
    import re

    from app.services.myinvois import qr_extractor

    src = inspect.getsource(qr_extractor._decode_pixmap)
    iv = re.search(r"zxingcpp\.ImageView\(\s*([A-Za-z_][\w.]*)", src)
    assert iv, "未找到 ImageView 调用，测试锚点失效，请同步更新本用例"
    first_arg = iv.group(1)
    assert first_arg != "pix.samples", (
        "ImageView 第一参数不得是 pix.samples —— 那是临时 bytes，零拷贝视图会悬垂")
    assert "." not in first_arg, (
        f"ImageView 第一参数必须是局部变量（实得 {first_arg!r}）："
        "属性访问会产生临时对象，构造函数返回后即被回收")
    assert re.search(rf"\b{re.escape(first_arg)}\s*=\s*pix\.samples_mv\b", src), (
        f"局部变量 {first_arg!r} 必须来自 pix.samples_mv（MuPDF 缓冲区上的 memoryview）")
