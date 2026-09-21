"""从 PDF 中提取 MyInvois 匿名验证链接 —— **不使用 OCR**。

马来西亚 e-Invoice 经税局验证后，票面会带一个匿名验证链接（Anonymous
Validation Link），形如：

    https://myinvois.hasil.gov.my/{UUID}/share/{SHARE_TOKEN}

三个来源，按可靠度降序尝试：
  1. **PDF 链接注记**（`/Annots` → `/A /URI`）—— 数字化生成的 e-Invoice 基本都有，
     100% 精确、零歧义。实测 MY-test 73 份样本中 15 份命中，全部来自此通道。
  2. **文本层**（`page.get_text()`）—— 链接以纯文本印在票面上时。
  3. QR 码图像解码 —— **本模块不做**：需额外 C 依赖（zbar/opencv），且前两条
     已覆盖数字化票据。扫描件走 OCR 主链路即可，不在此强行补齐。

一份 PDF 可能含多张票据（实测某样本 1 个 PDF 内 7 个链接），因此返回值带
`page`，供下游按页码与 OCR 记录对齐。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 允许生产与 preprod 两个域；其余域一律忽略（防止票面上的第三方链接误入）。
_HOST_RE = r"(?:preprod-)?myinvois\.hasil\.gov\.my"
# UUID 实测为 26 位大写字母数字；留出 20–32 的宽容区间。
# share token 实测 40+ 位、大小写混合。
_LINK_RE = re.compile(
    rf"https?://{_HOST_RE}/+"
    rf"(?:documents?/)?"                    # 少数实现带 /documents 前缀
    rf"(?P<uuid>[0-9A-Z]{{20,32}})"
    rf"/share/"
    rf"(?P<token>[0-9A-Za-z_-]{{20,64}})",
    re.IGNORECASE,
)

# 第二类版式（preprod 实测，金蝶等 ERP 自出的 e-Invoice PDF）：票面**不印**匿名
# 分享链接，而是把税局 UUID 作为一个标签字段印出来，例如
#
#     Unique ID No.
#     R4M6XHDBQFKEK46WS10C5Q2M10
#
# 对「带 token 认证」的 GET /documents/{uuid}/raw 来说，**UUID 就够了**
# （share token 只用于匿名验证页）。因此这类票同样可以查验，必须识别。
# 标签部分大小写不敏感（实测有 "Unique ID No."/"Unique Identifier No"/
# "Unique Identifier Number:" 多种写法）；**值部分必须限定大写+数字**，
# 否则 IGNORECASE 会让它一路吃进后面的小写单词（如 "Validation"）。
#
# 值里允许夹空白：PDF 文本层会把 UUID 断行，实测
# "…WR8RG60K1\n0 Validation…" 使末位 0 掉出来，得到 25 位的残缺 UUID
# —— 残缺 UUID 拿去查税局必然 404。故先宽松吃、去空白、再按 26 位定长裁切。
_UUID_LEN = 26
_UUID_LABEL_RE = re.compile(
    r"(?i:Unique\s*(?:ID|Identifier)\s*(?:No\.?|Number)?|UUID)"
    r"\s*[:：]?\s*"
    r"(?P<uuid>[0-9A-Z][0-9A-Z\s]{20,44})"
)


def _normalize_uuid(captured: str) -> str | None:
    """去空白 + 定长裁切；拿不到合规长度就返回 None（宁缺勿错）。"""
    s = re.sub(r"\s+", "", captured or "")
    if len(s) >= _UUID_LEN:
        s = s[:_UUID_LEN]
    elif len(s) < 20:
        return None
    # 必须字母+数字混合 —— 排除把普通英文词/纯数字当成 UUID
    if not (re.search(r"[0-9]", s) and re.search(r"[A-Z]", s)):
        return None
    return s


@dataclass(frozen=True)
class MyInvoisRef:
    """票面上的一条税局单据引用。

    `share_token` 只有匿名验证链接才有；「标签式 UUID」版式下为空字符串
    —— 认证取数只需要 uuid，不影响查验。
    """

    uuid: str
    share_token: str = ""
    page: int = 1             # 1-based，与 OCR 输出的 page 数组同基准
    source: str = "annotation"  # annotation | text | text-uuid
    url: str = ""

    def as_dict(self) -> dict:
        # 注意：share_token 是分享密钥，日志/回执里只给前 8 位。
        return {
            "uuid": self.uuid,
            "share_token_prefix": self.share_token[:8],
            "page": self.page,
            "source": self.source,
        }


def extract_refs(pdf_bytes: bytes) -> list[MyInvoisRef]:
    """扫描 PDF 全部页面，返回去重后的验证链接（按页码、出现顺序）。

    非 PDF、加密、损坏一律返回空列表——调用方据此跳过查验，不报错。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:  # pragma: no cover - 依赖缺失时静默降级
        logger.warning("PyMuPDF 不可用，跳过 MyInvois 链接提取")
        return []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.info("MyInvois 链接提取：无法打开为 PDF（%s），跳过", exc)
        return []

    refs: list[MyInvoisRef] = []
    seen: set[tuple[str, str]] = set()

    try:
        for page_index, page in enumerate(doc, start=1):
            # ① 链接注记
            try:
                links = page.get_links() or []
            except Exception:
                links = []
            for link in links:
                uri = link.get("uri") or ""
                for ref in _scan(uri, page_index, "annotation"):
                    key = (ref.uuid, ref.share_token)
                    if key not in seen:
                        seen.add(key)
                        refs.append(ref)

            # ② 文本层（换行可能把 URL 截断，先压平空白）
            try:
                raw_text = page.get_text() or ""
            except Exception:
                raw_text = ""
            if raw_text:
                flat = re.sub(r"\s+", "", raw_text)
                for ref in _scan(flat, page_index, "text"):
                    key = (ref.uuid, ref.share_token)
                    if key not in seen:
                        seen.add(key)
                        refs.append(ref)

                # ③ 标签式 UUID（无分享链接的 ERP 自出版式）。在**原始**文本上
                # 匹配，因为标签与值之间通常是换行。
                for m in _UUID_LABEL_RE.finditer(raw_text):
                    u = _normalize_uuid(m.group("uuid"))
                    if not u:
                        continue
                    if any(r.uuid == u for r in refs):
                        continue                    # 已由链接注记拿到，别重复
                    key = (u, "")
                    if key not in seen:
                        seen.add(key)
                        refs.append(MyInvoisRef(
                            uuid=u, share_token="", page=page_index,
                            source="text-uuid", url="",
                        ))
    finally:
        doc.close()

    if refs:
        logger.info(
            "MyInvois 链接提取：命中 %d 条（页码 %s）",
            len(refs), sorted({r.page for r in refs}),
        )
    return refs


def _scan(text: str, page: int, source: str) -> list[MyInvoisRef]:
    out: list[MyInvoisRef] = []
    for m in _LINK_RE.finditer(text or ""):
        out.append(
            MyInvoisRef(
                uuid=m.group("uuid").upper(),
                share_token=m.group("token"),
                page=page,
                source=source,
                url=m.group(0),
            )
        )
    return out
