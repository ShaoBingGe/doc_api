"""Gemini 账号的真实并发能力探测 —— 回答「提速该往哪儿使劲」。

已排除的方向（实测，见 downscale_probe.py）：
  · 下采样图片**无效**：prompt token 在原图 12.5MP 与 864×1152 下**完全相同**
    （均 7935）——Gemini 在服务端已把图像归一化，客户端缩图省不到 token；
    且缩图把 Maybank 回执的 docType 从 invoice 翻成了 receipt（准确率反而降）。

本脚本测两件事：
  1. **单次延迟的构成**：同一份文档串行重复 N 次，看延迟的自然波动范围
     （区分「模型固有耗时」与「抖动」）；
  2. **并发扩展性**：并发度 1 → C 时，单次延迟是否被拉长、总吞吐是否线性上升、
     是否触发 429。这决定了「加并发」到底能不能换来吞吐。

用法：cd backend && python eval_samples/chinkin_batch/concurrency_probe.py
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")

from app.ocr_optimizer.service import template_loader              # noqa: E402
from app.ocr_optimizer.service.preset_init import build_v1_prompt  # noqa: E402
from app.processors.gemini_processor import GeminiProcessor        # noqa: E402
from app.processors import key_pool                                # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SAMPLES = REPO / "sample_chinkin"
HERE = pathlib.Path(__file__).parent

DOC = SAMPLES / "IMG_20260821_161157.jpg"      # 3 行明细的中等复杂度票据
SERIAL_N = 4                                    # 串行基线次数
LEVELS = [2, 4, 8]                              # 并发度档位


def one_call(proc, prompt, schema) -> tuple[float, str | None, int | None]:
    t0 = time.time()
    try:
        proc.process_document(str(DOC), prompt, {"response_schema": schema})
        tok = (proc.last_usage or {}).get("prompt_tokens")
        return time.time() - t0, None, tok
    except Exception as exc:                                   # noqa: BLE001
        return time.time() - t0, f"{type(exc).__name__}: {str(exc)[:90]}", None


def main() -> None:
    d = template_loader.decompose_country_template("MY")
    prompt, schema = build_v1_prompt(d), d["json_schema"]

    print(f"key 池成员数: {len(key_pool.keys_for('gemini'))}")
    print(f"探测文档: {DOC.name}（{DOC.stat().st_size // 1024} KB）\n")

    out: dict = {}

    # ── 1. 串行基线 ──
    print(f"═══ 串行基线（{SERIAL_N} 次）═══")
    proc = GeminiProcessor()
    serial = []
    for i in range(SERIAL_N):
        dt, err, tok = one_call(proc, prompt, schema)
        serial.append(dt)
        print(f"  #{i+1}: {dt:5.1f}s  tok={tok}" + (f"  ⚠️ {err}" if err else ""))
    base = statistics.median(serial)
    print(f"  → 中位 {base:.1f}s，波动区间 {min(serial):.1f}–{max(serial):.1f}s "
          f"（极差 {max(serial)-min(serial):.1f}s）")
    out["serial"] = {"times": [round(x, 1) for x in serial], "median": round(base, 1)}

    # ── 2. 并发扩展性 ──
    for c in LEVELS:
        print(f"\n═══ 并发 {c} ═══")
        procs = [GeminiProcessor() for _ in range(c)]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=c) as ex:
            res = list(ex.map(lambda p: one_call(p, prompt, schema), procs))
        wall = time.time() - t0
        lat = [r[0] for r in res]
        errs = [r[1] for r in res if r[1]]
        med = statistics.median(lat)
        print(f"  单次延迟 中位 {med:5.1f}s（{min(lat):.1f}–{max(lat):.1f}s）")
        print(f"  墙钟 {wall:5.1f}s  →  吞吐 {c/wall*60:5.1f} 张/分钟"
              f"（串行同期 {1/base*60:.1f} 张/分钟）")
        print(f"  延迟相对串行基线: {med/base:.2f}×  "
              + ("✅ 未见排队" if med < base * 1.35 else "⚠️ 明显被拉长"))
        if errs:
            print(f"  ❌ 失败 {len(errs)}/{c}: {errs[:2]}")
        out[f"concurrency_{c}"] = {
            "latencies": [round(x, 1) for x in lat], "median": round(med, 1),
            "wall_s": round(wall, 1), "throughput_per_min": round(c / wall * 60, 1),
            "errors": errs,
        }

    (HERE / "concurrency_probe_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n→ {HERE / 'concurrency_probe_results.json'}")


if __name__ == "__main__":
    main()
