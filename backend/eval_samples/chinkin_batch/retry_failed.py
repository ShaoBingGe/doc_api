"""重试首轮未拿到识别结果的样本，并把结果并回进度明细。

首轮两类失败：
  · HTTP 502 —— 网关超时（Gemini 偶发 503「Deadline expired」拖长单次请求），
    与票面内容无关，直接重试即可；
  · errcode=5000 —— 文件真实内容是 PNG 却叫 .jpg，服务端「文件头与扩展名
    必须一致」的校验拦下（服务端行为正确，是**样本文件本身命名有误**）。
    重试时按真实类型申报为 image/png，让它进到识别环节。

用法：cd backend && python eval_samples/chinkin_batch/retry_failed.py
"""

from __future__ import annotations

import json
import pathlib
import time

from run_batch import (  # noqa: E402  —— 同目录复用，避免调用逻辑两份实现
    CREDS,
    PROGRESS,
    SAMPLES,
    OUT_DIR,
    analyze,
    get_token,
    now_str,
    summarize,
)

MAX_ATTEMPTS = 3
BACKOFF_S = 10


def main() -> None:
    recs = json.loads(PROGRESS.read_text())
    todo = [r for r in recs if r.get("errcode") not in ("0000", "4009")]
    if not todo:
        print("没有需要重试的样本")
        return

    creds = json.loads(CREDS.read_text())
    cid, secret = creds["client_id"], creds["client_secret"]
    tok = get_token(cid, secret)
    print(f"待重试 {len(todo)} 份\n")

    by_idx = {r["idx"]: r for r in recs}
    for r in todo:
        f = SAMPLES / r["file"]
        # 真实类型是 PNG 的那张，按 PNG 申报（首轮被扩展名校验拦下）
        as_png = f.read_bytes()[:4] == b"\x89PNG"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            submitted_at = now_str()
            t0 = time.time()
            try:
                payload = analyze(tok, cid, f, force_png=as_png)
                err = None
            except Exception as exc:  # noqa: BLE001
                payload = {"_transport_error": f"{type(exc).__name__}: {exc}"}
                err = payload["_transport_error"]
            elapsed = time.time() - t0
            received_at = now_str()
            s = summarize(payload) if err is None else {
                "errcode": None, "errmsg": err, "n": 0, "entities": []}

            done = s.get("errcode") in ("0000", "4009")
            mark = "✅" if s.get("errcode") == "0000" else ("⚠️" if s.get("errcode") else "❌")
            print(f"  {mark} [{r['idx']:2}] {r['file']} 第{attempt}次 {elapsed:6.1f}s "
                  f"errcode={s.get('errcode')} n={s['n']}")

            if done or attempt == MAX_ATTEMPTS:
                (OUT_DIR / f"{f.stem}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                rec = by_idx[r["idx"]]
                rec.update({"submitted_at": submitted_at, "received_at": received_at,
                            "elapsed_s": round(elapsed, 2), "retried": attempt,
                            "declared_png": as_png, **s})
                break
            time.sleep(BACKOFF_S)
        time.sleep(2)

    PROGRESS.write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    print(f"\n已并回 {PROGRESS}")


if __name__ == "__main__":
    main()
