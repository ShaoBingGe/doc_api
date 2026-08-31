"""sample_chinkin 批量识别 —— 顺序调用腾讯云生产端点，每张间隔 2 秒。

走公网入口（Caddy 反代 `/base/*` `/ai/*` → 127.0.0.1:8000），与对接方
调用的是同一条链路、同一个 templateId=7 的 MY 模板（英文版 7_16）。

每张票据：
  · 结果 JSON 原样落盘 sample_chinkin_results/<同名>.json
  · 记录 提交时间 / 取得结果时间 / 总耗时，供日志 md 汇总

`fileHash` 用文件真实 md5（接口的原本语义）——同一文件 15 分钟内重复提交
会命中服务端缓存，这正是对接方的真实行为，不做规避。

用法：cd backend && python eval_samples/chinkin_batch/run_batch.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

BASE = "http://129.226.145.150"
CREDS = pathlib.Path("/tmp/chinkin_creds.json")
REPO = pathlib.Path(__file__).resolve().parents[3]
SAMPLES = REPO / "sample_chinkin"
OUT_DIR = REPO / "sample_chinkin_results"
PROGRESS = pathlib.Path("/tmp/chinkin_batch_progress.json")

INTERVAL_S = 2.0          # 用户要求：每张之间保持 2 秒间隔
REQUEST_TIMEOUT_S = 600   # 与 Caddy 的 read_timeout 对齐

CST = timezone(timedelta(hours=8))   # 记录时间用本地（东八区）便于对账


def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def post_json(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def get_token(cid: str, secret: str) -> str:
    ts = str(int(time.time()))
    sign = hashlib.md5(f"{cid}{secret}{ts}".encode()).hexdigest()
    r = post_json("/base/oauth/token",
                  {"client_id": cid, "timestamp": ts, "sign": sign})
    if r.get("errcode") != "0000":
        raise SystemExit(f"取 token 失败: {r}")
    return r["access_token"]


def analyze(tok: str, cid: str, f: pathlib.Path, *, force_png: bool = False) -> dict:
    """force_png：样本扩展名是 .jpg 但实际内容是 PNG 时，按真实类型申报，
    否则会被服务端「文件头须与扩展名一致」的校验挡在识别之前（errcode=5000）。"""
    data = f.read_bytes()
    boundary = uuid.uuid4().hex
    fields = {"templateId": "7", "clientId": cid,
              "fileHash": hashlib.md5(data).hexdigest()}
    fname, ctype = (f"{f.stem}.png", "image/png") if force_png else (f.name, "image/jpeg")
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{k}"\r\n\r\n{v}\r\n').encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f'filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n').encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{BASE}/ai/knowledge/nlpService/document/analyze?access_token={tok}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.loads(urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S).read())


def summarize(payload: dict) -> dict:
    """压成日志用的一行摘要（完整结果已单独落盘）。"""
    ents = payload.get("data") or []
    rows = []
    for e in ents:
        b = (e.get("header") or {}).get("basic") or {}
        bf = (e.get("header") or {}).get("billFrom") or {}
        rows.append({
            "docType": b.get("docType"),
            "invoiceType": b.get("invoiceType"),
            "nameOfInvoice": b.get("nameOfInvoice"),
            "invoiceNumber": b.get("invoiceNumber"),
            "invoiceDate": b.get("invoiceDate"),
            "currency": b.get("currency"),
            "totalAmount": b.get("totalAmount"),
            "billFromName": bf.get("billFromName"),
            "lineRows": len((e.get("detail") or {}).get("detailOfGoodsOrServices") or []),
        })
    return {"errcode": payload.get("errcode"),
            "errmsg": payload.get("errmsg"),
            "n": len(ents), "entities": rows}


def main() -> None:
    creds = json.loads(CREDS.read_text())
    cid, secret = creds["client_id"], creds["client_secret"]
    tok = get_token(cid, secret)

    OUT_DIR.mkdir(exist_ok=True)
    files = sorted(SAMPLES.glob("*.jpg"))
    print(f"共 {len(files)} 张 · 输出目录 {OUT_DIR} · 间隔 {INTERVAL_S}s", flush=True)

    records: list[dict] = []
    for i, f in enumerate(files, 1):
        submitted_at = now_str()
        t0 = time.time()
        try:
            payload = analyze(tok, cid, f)
            err = None
        except urllib.error.HTTPError as exc:          # 4xx/5xx：HTTP 层失败
            payload = {"_transport_error": f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"}
            err = payload["_transport_error"]
        except Exception as exc:                        # 超时 / 连接中断
            payload = {"_transport_error": f"{type(exc).__name__}: {exc}"}
            err = payload["_transport_error"]
        elapsed = time.time() - t0
        received_at = now_str()

        (OUT_DIR / f"{f.stem}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        s = summarize(payload) if err is None else {"errcode": None, "n": 0,
                                                    "entities": [], "errmsg": err}
        rec = {"idx": i, "file": f.name, "size_kb": round(f.stat().st_size / 1024),
               "submitted_at": submitted_at, "received_at": received_at,
               "elapsed_s": round(elapsed, 2), **s}
        records.append(rec)
        PROGRESS.write_text(json.dumps(records, ensure_ascii=False, indent=1))

        mark = "✅" if s.get("errcode") == "0000" else ("⚠️" if s.get("errcode") else "❌")
        types = "/".join(filter(None, (r["docType"] for r in s["entities"]))) or "-"
        print(f"  [{i:2}/{len(files)}] {mark} {f.name}  {elapsed:6.1f}s  "
              f"errcode={s.get('errcode')} n={s['n']} [{types}]", flush=True)

        if i < len(files):
            time.sleep(INTERVAL_S)

    print(f"\n完成，明细 {PROGRESS}", flush=True)


if __name__ == "__main__":
    main()
