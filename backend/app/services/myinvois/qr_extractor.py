"""从 PDF 中解码 MyInvois 二维码 —— 高质量、可控成本的实现。

为什么二维码是主路径（实测，chinkin 108 份）：
    二维码可解 80 份 (74.1%)  ·  旧的链接注记/文字层方法 49 份 (45.4%)
    旧方法能提到、二维码却解不出的：**0 份**（二维码是严格超集）
而且二维码里同时含 uuid 与 share token（= longId），文字层只有 uuid ——
longId 是网关查验接口的必填入参，走网关列表只有 4/30 能拿到。

实现要点（都对应实测踩到的坑）：

1. **零拷贝解码**：用 `zxingcpp.ImageView` 直接包 PyMuPDF 的 `pix.samples_mv`
   （MuPDF 缓冲区上的 memoryview；`pix.samples` 是**拷贝**，且用作临时对象时
   会让零拷贝视图悬垂 —— 见 `_decode_pixmap` 内注释），不走 PIL 的 PNG
   编解码往返。实测这是整个流程最大的一笔浪费。
2. **只认 QRCode 格式**：`formats=QRCode` 让 zxing 跳过其余几十种码制的搜索。
3. **成本递增的阶梯**，每级都可能提前命中就收工：
   ① 内嵌位图（几乎免费，且先用「像不像二维码」做廉价预筛，避免对 logo/照片解码）
   ② 整页渲染 150 DPI（矢量绘制的二维码只能这样拿到 —— 实测 INV_WMSB 就是）
   ③ 整页渲染 300 DPI（小尺寸/低质量码的兜底）
   ④ 候选方块区域裁切放大（最后手段，只对少数难例付高 DPI 成本）
4. **命中即止**：某页已解出 MyInvois 链接就不再对该页升级，不做无谓渲染。
5. **只接受 MyInvois 域名**，防止把票面上第三方二维码（银行/物流）误当税局引用。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except ImportError:  # pragma: no cover
    _HAS_FITZ = False

try:
    import zxingcpp
    _HAS_ZXING = True
except ImportError:  # pragma: no cover
    _HAS_ZXING = False


# 生产 myinvois / 沙箱 preprod-（横线）与 preprod.（点）三种都见过
_HOST = r"(?:preprod[-.])?myinvois\.hasil\.gov\.my"
_URL_RE = re.compile(
    rf"https?://{_HOST}/+(?:documents?/)?"
    rf"(?P<uuid>[0-9A-Z]{{20,32}})"
    rf"(?:/share/(?P<token>[0-9A-Za-z_-]{{20,64}}))?",
    re.IGNORECASE,
)

# 阶梯参数 —— 由真实语料模拟选出（209 页，含 167 页需渲染）：
#   [150,300] 无门控      95/96 命中, 6847ms
#   [150,250,300] +门控   96/96 命中, 6533ms  ← 覆盖更全且更省，故采用
# 250 这一档是必需的：有 1 页 150 与 300 都解不出、唯独 250 能解
# （降采样与码元栅格的相位关系所致，不是玄学，换一份文件会换一个 DPI 命中）。
_DPI_BASE = 150              # 基础档：无条件跑，实测独立命中占大头（54/60）
_DPI_ESCALATE = (250, 300)   # 升级档：仅在页面有方形候选时才付这笔成本
#
# 为什么 250 这一档不能省：有 1 页 150 与 300 都解不出、唯独 250 能解
# （降采样相位与码元栅格的关系，换份文件会换个 DPI 命中，不是玄学）。
# 门控后全覆盖 96/96 且比"三档全跑"还省 24%（6533ms → 4937ms，真实语料模拟）。
_CROP_DPI = 600
# 裁切后长边像素上限。裁切档按 rect 实际尺寸自适应降 DPI，绝不为一个大 rect
# 渲染出巨幅 Pixmap：600 DPI 下一个 841×599pt 的整页背景图 ≈ 7008×4992×3 ≈ 105 MB，
# 3 路并发叠在 OCR 渲染上就是红线⑦要防的 OOM。真二维码（≈120pt）不受影响。
_CROP_MAX_PX = 1600
# 二维码扫描段自身的时间预算（秒）。MYINVOIS_BUDGET_S 只管 verify 循环，
# 扫描段无上限时，一份 120 页 PDF 能让线程空转 50-60s 把池占满。
_SCAN_BUDGET_S = 12.0
_MIN_IMG = 40           # 小于此边长的位图不可能是可解的二维码
_MAX_IMG = 2000
_CAND_MIN_PT = 20       # 裁切候选的边长下限（PDF point）
_CAND_MAX_PT = 300      # 上限：超过此尺寸的块不可能是票面二维码


@dataclass(frozen=True)
class QrHit:
    """一条从二维码解出的税局单据引用。"""

    uuid: str
    long_id: str          # share token == MyInvois longId；缺失时为 ""
    page: int             # 1-based
    url: str
    method: str           # embedded | render150 | render300 | crop600

    def as_dict(self) -> dict:
        # long_id 是分享密钥，日志/回执只给前缀
        return {
            "uuid": self.uuid,
            "long_id_prefix": self.long_id[:8],
            "has_long_id": bool(self.long_id),
            "page": self.page,
            "method": self.method,
        }


# ── 解码原语 ────────────────────────────────────────────────────────────────
def _fmt_for(pix):
    """PyMuPDF Pixmap → zxing ImageFormat；无法直接表达的返回 None（交给 RGB 兜底）。

    `n == 4` 既可能是 RGBA 也可能是 **CMYK**，必须看 `alpha` 才能区分。早先只按
    通道数判定，CMYK 位图会被当成 RGBA 硬解 —— 通道语义对不上，注释里声称的
    「CMYK → 转 RGB」兜底分支永远走不到，内嵌位图这条最便宜的快路径在 CMYK
    票面上必然落空（白白退到整页渲染）。
    """
    n, alpha = pix.n, bool(getattr(pix, "alpha", 0))
    if n == 1 and not alpha:
        return zxingcpp.ImageFormat.Lum
    if n == 3 and not alpha:
        return zxingcpp.ImageFormat.RGB
    if n == 4 and alpha:
        return zxingcpp.ImageFormat.RGBA
    return None                          # 灰度+alpha / CMYK / 其他 → 转 RGB


def _decode_pixmap(pix) -> list[str]:
    """零拷贝解码一个 PyMuPDF Pixmap，返回解出的文本列表。"""
    if pix.width < _MIN_IMG or pix.height < _MIN_IMG:
        return []
    fmt = _fmt_for(pix)
    if fmt is None:                                  # CMYK 等 → 转 RGB
        try:
            pix = fitz.Pixmap(fitz.csRGB, pix)
            fmt = zxingcpp.ImageFormat.RGB
        except Exception:
            return []
    try:
        # ⚠️ 必须用 samples_mv 且**绑到局部变量**，两点缺一不可：
        #   · ImageView 是零拷贝，只存裸指针、不持有 Python 引用；
        #     若传 `pix.samples`（每次访问新建一个临时 bytes），该临时对象在
        #     构造函数返回时就被回收 → read_barcodes 读已释放内存。
        #   · 症状随分配器状态在「段错误」与「静默解不出码」之间摇摆，
        #     且 `except Exception` **拦不住段错误**。
        #     macOS 侥幸不复现，Rocky 9 / Linux x86_64 必现（2026-09-21 实测）。
        #   · samples_mv 是 MuPDF 自身缓冲区上的 memoryview（真零拷贝），
        #     生命周期系于 pix —— pix 是活着的局部变量，故安全。
        buf = pix.samples_mv
        view = zxingcpp.ImageView(
            buf, pix.width, pix.height, fmt, row_stride=pix.stride
        )
        res = zxingcpp.read_barcodes(
            view,
            formats=zxingcpp.BarcodeFormat.QRCode,   # 只找二维码，省掉其余码制搜索
            try_rotate=True,                         # 票面二维码可能旋转
            try_invert=True,                         # 反白打印
            try_downscale=True,
        )
        return [r.text for r in res if r.text]
    except Exception:
        return []


def _looks_like_qr(pix) -> bool:
    """廉价预筛：二维码几乎纯黑白 + 横向跳变密集；logo 是平滑曲线。

    实测标定：真二维码 极值比=1.000 跳变率=0.149；公司 logo 0.856/0.089。
    预筛只用来**跳过明显不是码的大图**，阈值取得很松，宁可多解几张。
    """
    w, h = pix.width, pix.height
    if not (_MIN_IMG <= w <= _MAX_IMG and _MIN_IMG <= h <= _MAX_IMG):
        return False
    if abs(w - h) > max(8, 0.25 * max(w, h)):        # 二维码近正方
        return False
    try:
        s, n = pix.samples_mv, pix.n      # memoryview：预筛不该拷贝整幅图
        ex = tot = tr = ce = 0
        for y in range(0, h, max(1, h // 24)):
            prev = None
            for x in range(0, w, max(1, w // 48)):
                i = (y * pix.stride) + x * n
                g = s[i] if n == 1 else (s[i] + s[i + 1] + s[i + 2]) // 3
                tot += 1
                if g < 70 or g > 185:
                    ex += 1
                b = 1 if g < 128 else 0
                if prev is not None:
                    ce += 1
                    tr += (b != prev)
                prev = b
        if not tot or not ce:
            return False
        return (ex / tot) > 0.80 and (tr / ce) > 0.06
    except Exception:
        return True                                   # 判不了就别拦，交给解码器


def _parse(texts: list[str], page: int, method: str) -> list[QrHit]:
    hits: list[QrHit] = []
    for t in texts:
        m = _URL_RE.search(t or "")
        if not m:
            continue                                  # 非税局二维码（银行/物流等）→ 忽略
        hits.append(QrHit(
            uuid=m.group("uuid").upper(),
            long_id=m.group("token") or "",
            page=page, url=m.group(0), method=method,
        ))
    return hits


def _square_candidates(page) -> list:
    """页面上疑似二维码的方形区域（位图 + 矢量图形块），供裁切放大兜底。"""
    def _square_enough(r) -> bool:
        """二维码近正方且尺寸有限。位图与矢量块**同一把尺子** —— 早先位图分支
        不做任何过滤，整页背景图也会进候选，于是「方形候选门控」形同虚设，
        几乎每页都放行到最贵的裁切档（实测 DOC_07_15_25006：候选全是
        841×599 / 150×573 这类非方形块）。"""
        return (r is not None
                and _CAND_MIN_PT <= r.width <= _CAND_MAX_PT
                and abs(r.width - r.height) <= 0.3 * r.width)

    rects = []
    try:
        for im in page.get_images(full=True):
            for r in page.get_image_rects(im[0]) or []:
                if _square_enough(r):
                    rects.append(r)
    except Exception:
        pass
    try:
        for d in page.get_drawings() or []:
            if _square_enough(d.get("rect")):
                rects.append(d["rect"])
    except Exception:
        pass
    # 合并相近矩形，限制数量
    out = []
    for r in rects:
        if r.width < _CAND_MIN_PT or r.height < _CAND_MIN_PT:
            continue
        if any(abs(r.x0 - o.x0) < 5 and abs(r.y0 - o.y0) < 5 for o in out):
            continue
        out.append(r)
    return out[:6]


# ── 对外入口 ────────────────────────────────────────────────────────────────
def extract_qr_hits(
    pdf_bytes: bytes,
    *,
    max_pages: int | None = None,
    budget_s: float | None = _SCAN_BUDGET_S,
) -> list[QrHit]:
    """扫描 PDF 页面，返回去重后的税局二维码引用（按页码、出现顺序）。

    非 PDF / 加密 / 损坏 / 缺依赖 一律返回空列表 —— 调用方据此走纯 OCR，不报错。

    `max_pages` 与 `budget_s` 是**必须存在的刹车**：本函数是纯 CPU 的整页渲染，
    跑在线程池里且 `future.cancel()` 对已启动任务无效。没有刹车时一份上百页的
    PDF 会把线程占死数十秒，后续查验全卡在 join 宽限期超时 —— 增强静默失效，
    机器却还在烧 CPU。超时按「已扫到的页」返回（部分结果优于无结果）。
    """
    if not (_HAS_FITZ and _HAS_ZXING):
        logger.warning("缺少 PyMuPDF / zxing-cpp，跳过二维码识别")
        return []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.info("二维码识别：无法打开为 PDF（%s），跳过", exc)
        return []

    hits: list[QrHit] = []
    seen: set[tuple[str, str]] = set()
    t_start = time.monotonic()

    def keep(new: list[QrHit]) -> bool:
        got = False
        for h in new:
            key = (h.uuid, h.long_id)
            if key in seen:
                continue
            # 同一 uuid 若先前只拿到空 token，后面拿到带 token 的则升级替换
            for i, old in enumerate(hits):
                if old.uuid == h.uuid and not old.long_id and h.long_id:
                    hits[i] = h
                    seen.add(key)
                    got = True
                    break
            else:
                seen.add(key)
                hits.append(h)
                got = True
        return got

    try:
        for pno, page in enumerate(doc, start=1):
            if max_pages and pno > max_pages:
                break
            if budget_s is not None and time.monotonic() - t_start > budget_s:
                logger.info("二维码识别：扫描预算 %.1fs 用尽，已扫 %d 页后停止",
                            budget_s, pno - 1)
                break
            hit_this_page = False

            # ① 内嵌位图（最便宜）
            for im in page.get_images(full=True):
                try:
                    pix = fitz.Pixmap(doc, im[0])
                except Exception:
                    continue
                if not _looks_like_qr(pix):
                    continue
                if keep(_parse(_decode_pixmap(pix), pno, "embedded")):
                    hit_this_page = True
            if hit_this_page:
                continue

            # ② 基础渲染档：无条件跑一次。矢量绘制的二维码取不到内嵌位图，
            # 只能靠渲染（实测 INV_WMSB 即如此），且这一档就吃掉大部分命中。
            try:
                if keep(_parse(_decode_pixmap(page.get_pixmap(dpi=_DPI_BASE)),
                               pno, f"render{_DPI_BASE}")):
                    continue
            except Exception:
                pass

            # ③④ 升级档与裁切档**都门控**在「页面上确实存在方形候选」之后：
            # 基础档没解出、页面上又没有任何方形位图/矢量块，那这页几乎不可能
            # 有二维码，再往上堆 DPI 纯属为无码页付费（无码页占 167 页中的多数）。
            cands = _square_candidates(page)
            if not cands:
                continue

            for dpi in _DPI_ESCALATE:
                try:
                    if keep(_parse(_decode_pixmap(page.get_pixmap(dpi=dpi)),
                                   pno, f"render{dpi}")):
                        hit_this_page = True
                        break
                except Exception:
                    continue
            if hit_this_page:
                continue

            for rect in cands:                      # ④ 裁切放大，最后手段
                try:
                    # 自适应封顶：保证裁切结果长边 ≤ _CROP_MAX_PX，不为大 rect 渲染巨幅图
                    side = max(float(rect.width), float(rect.height)) or 1.0
                    dpi = int(min(_CROP_DPI, 72.0 * _CROP_MAX_PX / side))
                    if dpi < 72:
                        continue                    # 再降就没有放大意义了
                    if keep(_parse(_decode_pixmap(page.get_pixmap(dpi=dpi, clip=rect)),
                                   pno, f"crop{dpi}")):
                        break
                except Exception:
                    continue
    finally:
        doc.close()

    if hits:
        logger.info("二维码识别：命中 %d 条（页码 %s，方式 %s）",
                    len(hits), sorted({h.page for h in hits}),
                    sorted({h.method for h in hits}))
    return hits
