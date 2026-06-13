"""字段焦点定位（第一步）：从原生 PDF 的文字层拿词级坐标，把抽取出的「值」
匹配回票面位置——**完全不依赖让抽取模型吐坐标**。

设计动机（见交互设计讨论）：
  国家模板 prompt 只让模型吐值（不含坐标，泛化好、不被瞎编污染），所以
  annotation 没有 bbox，前端点字段放大只能盲猜网格点 → 牛头不对马嘴。
  本模块把「定位」从「抽取」解耦：抽取照常，定位是事后的、确定性的字符串
  匹配（业界标准做法：Document AI / Textract / Form Recognizer 同构）。

覆盖范围（第一步）：
  - 原生 PDF（电子发票，有文字层）→ PyMuPDF `get_text("words")` 给词级坐标，
    零额外 API、本地、一次性。
  - 扫描件 / 图片 → 文字层为空，返回 []，所有字段 bbox 留 None →
    前端降级（不画点、不假放大）。扫描件的定位留给第二步（anchor_text / grounding）。

坐标统一归一化到 0-100 百分比（含 page，1-based），与前端 boundingBox
（{x,y,width,height,page} 百分比）直接对齐——与渲染 DPI 无关。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    _PYMUPDF = True
except ImportError:  # pragma: no cover
    _PYMUPDF = False

# 短文本精确拼接匹配的最大窗口（多数标量字段 ≤ 12 词）。
_MAX_WINDOW = 12
# 一页最多取多少词——异常长的页（合同附页）截断，发票场景远用不到。
_MAX_WORDS_PER_PAGE = 4000


@dataclass
class Word:
    text: str
    x: float       # 0-100 左
    y: float       # 0-100 上
    w: float       # 0-100 宽
    h: float       # 0-100 高
    page: int      # 1-based

    def to_dict(self) -> dict:
        return {"text": self.text, "x": self.x, "y": self.y,
                "w": self.w, "h": self.h, "page": self.page}

    @staticmethod
    def from_dict(d: dict) -> "Word":
        return Word(text=d["text"], x=d["x"], y=d["y"], w=d["w"], h=d["h"],
                    page=int(d.get("page", 1)))


# ── 词坐标抽取 ────────────────────────────────────────────────────────────────


def extract_words(storage_path: str) -> list[Word]:
    """抽原生 PDF 的词级 bbox（归一化 0-100）。

    非 PDF / 扫描件（文字层为空）/ PyMuPDF 不可用 → 返回 []（调用方据此降级）。
    永不抛异常：定位是增强功能，绝不能拖垮上传/识别主流程。
    """
    if not _PYMUPDF or not storage_path.lower().endswith(".pdf"):
        return []
    words: list[Word] = []
    try:
        with fitz.open(storage_path) as doc:
            for pno, page in enumerate(doc, start=1):
                pw = page.rect.width or 1.0
                ph = page.rect.height or 1.0
                # get_text("words"): (x0, y0, x1, y1, "word", block, line, wno)
                raw = page.get_text("words")
                for i, wd in enumerate(raw):
                    if i >= _MAX_WORDS_PER_PAGE:
                        break
                    x0, y0, x1, y1, txt = wd[0], wd[1], wd[2], wd[3], wd[4]
                    if not str(txt).strip():
                        continue
                    words.append(Word(
                        text=str(txt),
                        x=round(x0 / pw * 100, 3),
                        y=round(y0 / ph * 100, 3),
                        w=round((x1 - x0) / pw * 100, 3),
                        h=round((y1 - y0) / ph * 100, 3),
                        page=pno,
                    ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_words failed for %s: %s", storage_path, exc)
        return []
    return words


# ── 值 → 坐标匹配 ─────────────────────────────────────────────────────────────

_NORM_STRIP_RE = re.compile(r"[\s,\-–—:/$¥€£.]")


def _norm(s: str) -> str:
    """匹配用归一化：去空格/千分位逗号/破折号/货币符/冒号/斜杠/句点，转小写。
    句点也去（"Dia." → "dia"，"Sdn. Bhd." 对齐）——数字小数点的精度比较
    走 _as_number 数值路径，文本路径不依赖小数点。"""
    return _NORM_STRIP_RE.sub("", str(s or "")).lower()


# ── 日期匹配（票面 DMY/月名 vs 输出 ISO）─────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")
_NUM_DATE_RE = re.compile(r"(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})")
_DM_MON_RE = re.compile(r"(\d{1,2})\s*[-/ ]?\s*([A-Za-z]{3,9})\s*[-/, ]*\s*(\d{2,4})")
_MON_D_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{2,4})")


def _norm_year(y: int) -> int:
    return 2000 + y if y < 100 else y


def _parse_date_candidates(text: str) -> set[tuple[int, int, int]]:
    """从一段文本里抽出所有可能的 (year, month, day)。

    发票日期格式繁杂且 DMY/MDY 歧义（09/07 既可能 7月9日也可能 9月7日），
    所以对数字式日期把 DMY 和 MDY **都**作为候选——只要其中之一等于目标
    （目标来自 ISO，y/m/d 已确定）即命中，不会误配（错的那个候选不会等于目标）。
    """
    out: set[tuple[int, int, int]] = set()
    t = str(text or "")

    for m in _NUM_DATE_RE.finditer(t):
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 99:                              # YYYY-MM-DD
            out.add((a, b, c))
        if c > 31 or c > 99:                     # ?/?/YYYY → DMY 与 MDY 都试
            yr = _norm_year(c)
            if 1 <= b <= 12 and 1 <= a <= 31:
                out.add((yr, b, a))             # DMY
            if 1 <= a <= 12 and 1 <= b <= 31:
                out.add((yr, a, b))             # MDY

    for m in _DM_MON_RE.finditer(t):            # 9 Jul 2025 / 09-July-25
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            out.add((_norm_year(int(m.group(3))), mon, int(m.group(1))))
    for m in _MON_D_RE.finditer(t):             # Jul 9, 2025
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            out.add((_norm_year(int(m.group(3))), mon, int(m.group(2))))
    return out


def _locate_date(iso: str, ws: list[Word]) -> dict | None:
    """目标是 ISO 日期时的专项定位：扫单词及最多 4 连词拼接，解析日期候选，
    与目标 (y,m,d) 比对。"""
    parts = iso.split("-")
    target = (int(parts[0]), int(parts[1]), int(parts[2]))
    # 优先单词精确命中（"31/07/2025" 一个词 → 最紧的框，不含标签）
    for w in ws:
        if target in _parse_date_candidates(w.text):
            return _merge_bbox([w])
    # 跨词日期（"9" "Jul" "2025"）：拼接最多 4 词
    for i in range(len(ws)):
        joined = ""
        window: list[Word] = []
        for j in range(i, min(i + 4, len(ws))):
            if ws[j].page != ws[i].page:
                break
            joined += (" " if joined else "") + ws[j].text
            window.append(ws[j])
            if target in _parse_date_candidates(joined):
                return _merge_bbox(window)
    return None


def _as_number(s: str) -> float | None:
    """尽力把字符串解析为数值（容忍千分位/货币符/括号负数）。失败返回 None。"""
    t = str(s or "").strip()
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")  # 会计负数写法
    t = re.sub(r"[,\s$¥€£%()]", "", t)
    t = t.replace("–", "-").replace("—", "-")
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return None


def _merge_bbox(window: list[Word]) -> dict:
    """合并连续词的包围盒（取最小外接矩形），返回归一化 bbox + page。"""
    x0 = min(w.x for w in window)
    y0 = min(w.y for w in window)
    x1 = max(w.x + w.w for w in window)
    y1 = max(w.y + w.h for w in window)
    return {
        "x": round(x0, 3), "y": round(y0, 3),
        "width": round(x1 - x0, 3), "height": round(y1 - y0, 3),
        "page": window[0].page,
    }


def locate_value(value: str, words: list[Word]) -> dict | None:
    """在词序列里定位 value，返回归一化 bbox（{x,y,width,height,page}）或 None。

    两条匹配路径：
      1. 数值类（value 可解析为数字）：按数值相等匹配单词或连续词拼接——
         容忍 "6,000.00" / "RM6000" / "6000.0" 等格式差异。
      2. 文本类：归一化后做连续词窗口的拼接包含匹配。

    匹配不到（含扫描件 words 为空）返回 None —— 调用方应留 bbox 空让前端降级，
    **绝不返回猜测坐标**。多处命中时取第一个（第二步用 anchor_text 提精度）。
    """
    val = str(value or "").strip()
    if not val or not words:
        return None

    # 用 PyMuPDF 的原生阅读序（block→line→word，对表格更准）；不再按
    # round(y) 重排（会把表格同行不同列的词亚像素差异打乱）。
    ws = words

    # 日期专项：票面 09/07/2025 / 9 Jul 2025 vs 输出 ISO 2025-07-09
    if _ISO_DATE_RE.match(val):
        hit = _locate_date(val, ws)
        if hit:
            return hit
        # 日期没匹配到也不再走文本路径（ISO 串本身不会原样出现在票面）
        return None

    num = _as_number(val)
    if num is not None and len(val) <= 24:  # 长串不当数字（如发票号 IV-852866）
        hit = _locate_numeric(num, ws)
        if hit:
            return hit
        # 数字路径没中，可能值里带字母前缀（W1 529054 被规范成纯数字了），
        # 继续走文本路径兜底

    return _locate_text(val, ws)


def _locate_numeric(target: float, ws: list[Word]) -> dict | None:
    # 单词命中
    for w in ws:
        n = _as_number(w.text)
        if n is not None and abs(n - target) < 0.001:
            return _merge_bbox([w])
    # 连续词拼接命中（如 "RM" "6,000.00" 分成两词，或整数小数被拆）
    for i in range(len(ws)):
        joined = ""
        window: list[Word] = []
        for j in range(i, min(i + _MAX_WINDOW, len(ws))):
            if ws[j].page != ws[i].page:
                break
            joined += ws[j].text
            window.append(ws[j])
            n = _as_number(joined)
            if n is not None and abs(n - target) < 0.001:
                return _merge_bbox(window)
    return None


def _locate_text(val: str, ws: list[Word]) -> dict | None:
    target = _norm(val)
    if not target:
        return None
    # 单词整词命中（最常见：发票号、日期一个词）
    for w in ws:
        if _norm(w.text) == target:
            return _merge_bbox([w])
    # 连续词拼接：窗口拼接归一化串 == target 或 包含 target
    for i in range(len(ws)):
        joined = ""
        window: list[Word] = []
        for j in range(i, min(i + _MAX_WINDOW, len(ws))):
            if ws[j].page != ws[i].page:
                break
            joined += _norm(ws[j].text)
            window.append(ws[j])
            if joined == target:
                return _merge_bbox(window)
            if len(target) >= 4 and target in joined:
                # 拼接已覆盖目标（窗口可能略宽），接受——发票字段足够精确
                return _merge_bbox(window)
            if len(joined) > len(target) + 4:
                break  # 已超出目标长度，本起点无望
    # 长文本（地址/明细描述，跨多词甚至多行）：完整拼接匹配不现实
    # （OCR 换行、单引号、缩写差异）。改用「前缀词锚定 + 词数框范围」——
    # 焦点放大只需框到那块区域，不要求像素级精确。
    return _locate_long_text(val, ws)


def _locate_long_text(val: str, ws: list[Word]) -> dict | None:
    """长文本（≥3 词）锚定：用开头的显著词在词流定位起点，再按目标词数
    向后框出范围。容忍尾部 OCR 差异/换行——对焦够用。"""
    toks = [_norm(t) for t in re.split(r"\s+", str(val)) if _norm(t)]
    sig = [t for t in toks if len(t) >= 2]   # 跳过单字符噪声词
    if len(toks) < 3 or len(sig) < 2:
        return None
    a0, a1 = sig[0], sig[1]
    n = len(toks)
    nws = [_norm(w.text) for w in ws]
    for i in range(len(ws)):
        if nws[i] != a0 and not (len(a0) >= 4 and nws[i].startswith(a0)):
            continue
        # 第二个锚点须出现在起点后的小窗里（确认不是孤立同词）
        if a1 not in nws[i + 1: i + 5]:
            continue
        end = min(i + n, len(ws))
        # 不跨页
        window = [w for w in ws[i:end] if w.page == ws[i].page]
        if window:
            return _merge_bbox(window)
    return None


def locate_fields(
    fields: list[dict], words: list[Word],
) -> tuple[int, int]:
    """批量给 fields（list of {keyName, value, bbox}）补 bbox。原地修改：
    仅当某字段还没有 bbox 且匹配成功时写入。返回 (定位成功数, 总字段数)。"""
    if not words:
        return 0, len(fields)
    located = 0
    for f in fields:
        if f.get("bbox"):  # 已有坐标（模型给的 / 用户手动锚的）不覆盖
            located += 1
            continue
        v = f.get("value")
        if v is None or str(v).strip() == "":
            continue
        bbox = locate_value(str(v), words)
        if bbox:
            f["bbox"] = bbox
            located += 1
    return located, len(fields)
