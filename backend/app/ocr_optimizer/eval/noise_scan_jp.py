"""噪声量 N 实证（ADR §2.4-B / §1.3）—— 留出验证集大小 vs 估计稳定性。

N 的本质：留出验证（noise）样本要多少张，soft-Gate 的 val 均值估计才够稳，才能可靠区分
一条 edit 是真改进还是噪声。本脚本 OCR 一批真实 Japan-inv `val` 样本（真 qwen，一次性），
用 bootstrap 重采样测不同 n 下 val 估计的 1σ / ±95% 波动，找肘点定 N。

开发期一次性工具，不入生产路径。运行（Japan-inv 为公开基准、qwen 合规）：
  QWEN_API_KEY=sk-... QWEN_MODEL=qwen3-vl-plus \
    python -m app.ocr_optimizer.eval.noise_scan_jp [n_ocr]
"""
from __future__ import annotations

import statistics
import sys
from random import Random

from . import bench_japan_inv as B

NS = [3, 4, 6, 8, 9, 12, 16, 20]
BOOT_ITERS = 3000


def run(n_ocr: int = 24, seed: int = 42) -> dict:
    if not B.available():
        raise SystemExit("Japan-inv 不可用（需本地 train/val/test 语料）")
    pairs = B.load("val", k=n_ocr, seed=seed)
    fields = B.fair_fields("JP", pairs)
    predict = B.real_predictor("JP", "qwen")

    print(f"OCR {len(pairs)} 张 Japan-inv val（真 qwen，公平字段 {len(fields)} 个）…", flush=True)
    softs: list[float] = []
    for i, (pdf, gt) in enumerate(pairs, 1):
        pred = predict(pdf) or {}
        s = B.score_pred(pred, gt, fields)["soft"]
        softs.append(s)
        print(f"  [{i}/{len(pairs)}] {pdf.name[:38]:38s} soft={s:.3f}", flush=True)

    base_mean = statistics.mean(softs)
    base_sd = statistics.pstdev(softs)
    print(
        f"\n基线：{len(softs)} 样本 soft 均值={base_mean:.3f}  单样本 sd={base_sd:.3f}\n"
        f"{'n(noise)':>9} | {'val估计 1σ':>10} | {'±95% 半宽':>10} | 较上一档降幅"
    )

    rng = Random(seed)
    rows = []
    prev_hw = None
    for n in NS:
        if n > len(softs):
            continue
        means = [
            statistics.mean([softs[rng.randrange(len(softs))] for _ in range(n)])
            for _ in range(BOOT_ITERS)
        ]
        sd = statistics.pstdev(means)
        hw = 1.96 * sd  # ±95% 半宽
        drop = "" if prev_hw is None else f"-{(prev_hw - hw) * 100:.2f}pp"
        rows.append({"n": n, "sigma": round(sd, 4), "halfwidth_pp": round(hw * 100, 2)})
        print(f"{n:>9} | {sd:>10.4f} | {hw * 100:>8.1f}pp | {drop:>10}")
        prev_hw = hw

    return {
        "base_mean": round(base_mean, 4),
        "base_sd": round(base_sd, 4),
        "fields": len(fields),
        "n_ocr": len(softs),
        "rows": rows,
    }


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 24)
