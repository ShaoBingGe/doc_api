"""图像下采样对「识别耗时 / token / 准确性」的影响 —— 提速方案的实证依据。

背景：sample_chinkin 60 张实测平均 16.5s，且耗时与**输入文件大小**强相关
（<2MB 平均 12.7s → >3.2MB 平均 20.3s），与**输出票据数无关**（0 张 16.9s /
1 张 16.1s）。说明瓶颈在图像摄入侧，不在生成侧。这些是手机直出的 3060×4080
（12.5MP）照片，而 Gemini 视觉编码按 768×768 分块——12.5MP 要切 ~24 块。

本脚本对同一批票据按长边下采样到若干档，逐档测：
  · 端到端耗时
  · prompt token（视觉 token 占大头，直接反映摄入成本）
  · **识别结果是否与原图一致**（关键字段逐一比对；提速不能拿准确率换）

用法：cd backend && python eval_samples/chinkin_batch/downscale_probe.py
"""

from __future__ import annotations


import json
import pathlib
import sys
import time

sys.path.insert(0, ".")

import fitz                                                        # noqa: E402  PyMuPDF

from app.ocr_optimizer.service import template_loader              # noqa: E402
from app.ocr_optimizer.service.preset_init import build_v1_prompt  # noqa: E402
from app.processors.gemini_processor import GeminiProcessor        # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SAMPLES = REPO / "sample_chinkin"
HERE = pathlib.Path(__file__).parent

# 取内容最丰富的样本：字段越多，下采样导致的退化越容易暴露
CASES = [
    "IMG_20260821_164515",   # 11 行明细，inv TT01/26/202, 7088.65
    "IMG_20260824_110351",   #  9 行明细，inv TT02/26/142, 5247.27
    "IMG_20260821_161157",   #  3 行明细，inv TT01/26/193,  443.28
]
# None = 原图不缩放
LONG_EDGES = [None, 2048, 1536, 1152]


def resized(path: pathlib.Path, long_edge: int | None) -> tuple[bytes, tuple[int, int]]:
    """用 PyMuPDF 下采样（已是项目依赖，不额外引入 Pillow）。
    long_edge=None 时返回原始字节，不做任何重编码——保证「原图」这一档
    是真正的对照基准，不掺入 JPEG 二次压缩的影响。"""
    if long_edge is None:
        with fitz.open(path) as doc:
            r = doc[0].rect
        return path.read_bytes(), (round(r.width), round(r.height))

    with fitz.open(path) as doc:
        page = doc[0]
        w, h = page.rect.width, page.rect.height
        scale = min(1.0, long_edge / max(w, h))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        return pix.tobytes("jpeg", jpg_quality=90), (pix.width, pix.height)


def key_fields(out) -> dict:
    """抽出用于「有没有退化」判定的关键字段。"""
    ents = out if isinstance(out, list) else []
    ents = [e for e in ents if isinstance(e, dict)]
    if not ents:
        return {"n": 0}
    e = ents[0]
    rows = e.get("detailOfGoodsOrServices") or []
    return {
        "n": len(ents),
        "docType": e.get("docType"),
        "invoiceNumber": e.get("invoiceNumber"),
        "invoiceDate": e.get("invoiceDate"),
        "totalAmount": e.get("totalAmount"),
        "billFromName": e.get("billFromName"),
        "rows": len(rows),
        # 明细行的品名+金额序列：最敏感的退化探针
        "rowSig": [f"{(r.get('articleName') or '')[:18]}|{r.get('netAmount')}" for r in rows],
    }


def main() -> None:
    d = template_loader.decompose_country_template("MY")
    prompt, schema = build_v1_prompt(d), d["json_schema"]
    proc = GeminiProcessor()

    results: dict = {}
    for stem in CASES:
        src = SAMPLES / f"{stem}.jpg"
        print(f"\n═══ {stem} ═══", flush=True)
        base = None
        for le in LONG_EDGES:
            data, size = resized(src, le)
            tmp = HERE / f"_probe_{stem}_{le or 'orig'}.jpg"
            tmp.write_bytes(data)
            t0 = time.time()
            try:
                raw = proc.process_document(str(tmp), prompt,
                                            {"response_schema": schema})
                out = json.loads(raw)
                err = None
            except Exception as exc:                       # noqa: BLE001
                out, err = None, f"{type(exc).__name__}: {exc}"[:120]
            dt = time.time() - t0
            tmp.unlink(missing_ok=True)

            kf = key_fields(out) if out is not None else {"error": err}
            u = proc.last_usage or {}
            tag = "原图" if le is None else f"长边{le}"
            if base is None:
                base = kf
                verdict = "（基准）"
            else:
                same = all(kf.get(k) == base.get(k) for k in
                           ("n", "docType", "invoiceNumber", "invoiceDate",
                            "totalAmount", "rows", "rowSig"))
                verdict = "✅ 与原图一致" if same else "❌ 有差异"
            results[f"{stem}|{tag}"] = {"size_px": size, "kb": len(data) // 1024,
                                        "elapsed_s": round(dt, 1), "usage": u,
                                        "fields": kf, "verdict": verdict}
            print(f"  {tag:9} {size[0]}×{size[1]:<5} {len(data)//1024:>4}KB  "
                  f"{dt:5.1f}s  prompt_tok={u.get('prompt_tokens')}  {verdict}", flush=True)
            if verdict.startswith("❌"):
                for k in ("invoiceNumber", "totalAmount", "rows"):
                    if kf.get(k) != base.get(k):
                        print(f"      {k}: 原图={base.get(k)!r} → 本档={kf.get(k)!r}")

    (HERE / "downscale_probe_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n→ {HERE / 'downscale_probe_results.json'}")


if __name__ == "__main__":
    main()
