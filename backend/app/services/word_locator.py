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

# 匹配一个值最多跨多少个连续词——防 O(n²) 爆炸 + 抑制误匹配（一个字段值
# 极少跨 8 个以上 OCR 词）。
_MAX_WINDOW = 8
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

_NORM_STRIP_RE = re.compile(r"[\s,\-–—:/$¥€£]")


def _norm(s: str) -> str:
    """匹配用归一化：去空格/千分位逗号/破折号/货币符/冒号/斜杠，转小写。
    **保留小数点与数字**（去掉会把 6000.00 变 600000）。"""
    return _NORM_STRIP_RE.sub("", str(s or "")).lower()


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

    # 按 (page, y, x) 排序，保证「连续词」在阅读序上相邻
    ws = sorted(words, key=lambda w: (w.page, round(w.y, 1), w.x))

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
