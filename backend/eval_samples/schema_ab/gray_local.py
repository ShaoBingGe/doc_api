"""本地灰度验收 —— 走真实 HTTP 服务进程（非 TestClient）。

覆盖 Phase 4 runbook 的全部观察项，只是把目标从腾讯云换成本地 127.0.0.1:8000：
  1. 鉴权（MD5 sign）与 token
  2. 同步 `document/analyze`：真实识别 + 瘦身模板 7_16 生效
  3. 异步 `analyze/async` + `tasks/query`：提交→轮询→取结果全链路
  4. A0704 / 4009：全 other 文档判失败（不返回空壳成功）
  5. 25 页大文档：准入闸页数维度不误杀

用法：cd backend && .venv/bin/python eval_samples/schema_ab/gray_local.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, ".")

from app.core.database import SessionLocal          # noqa: E402
from app.models.open_api_client import OpenApiClient  # noqa: E402

BASE = "http://127.0.0.1:8000"
HERE = pathlib.Path(__file__).parent


def post_json(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def token(cid: str, secret: str) -> str:
    ts = str(int(time.time()))
    sign = hashlib.md5(f"{cid}{secret}{ts}".encode()).hexdigest()
    return post_json("/base/oauth/token",
                     {"client_id": cid, "timestamp": ts, "sign": sign})["access_token"]


def upload(path: str, tok: str, cid: str, f: pathlib.Path, extra: dict | None = None) -> dict:
    data = f.read_bytes()
    b = uuid.uuid4().hex
    fields = {"templateId": "7", "clientId": cid,
              "fileHash": f"gray-{uuid.uuid4().hex[:10]}"}
    fields.update(extra or {})
    body = b""
    for k, v in fields.items():
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{f.name}\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    body += data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"{BASE}{path}?access_token={tok}", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    return json.loads(urllib.request.urlopen(req, timeout=900).read())


def summarize(payload: dict) -> tuple[int, dict, list]:
    ents = payload.get("data") or []
    basics = [(e.get("header") or {}).get("basic") or {} for e in ents]
    from collections import Counter
    return (len(ents), dict(Counter(b.get("docType") for b in basics)),
            [b.get("invoiceNumber") for b in basics if b.get("docType") != "other"])


def main() -> None:
    db = SessionLocal()
    c = db.query(OpenApiClient).first()
    cid, secret = c.client_id, c.client_secret
    db.close()

    results: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))

    print("═══ 1. 鉴权 ═══")
    bad = post_json("/base/oauth/token",
                    {"client_id": cid, "timestamp": "1", "sign": "deadbeef"})
    check("错误 sign 被拒", bad.get("errcode") != "0000", f"errcode={bad.get('errcode')}")
    tok = token(cid, secret)
    check("正确 sign 换 token", bool(tok), f"len={len(tok)}")

    print("\n═══ 2. 同步识别（瘦身模板 7_16）═══")
    t0 = time.time()
    r = upload("/ai/knowledge/nlpService/document/analyze", tok, cid,
               HERE / "S1_single_invoice.pdf")
    n, types, nums = summarize(r)
    dt = time.time() - t0
    check("errcode=0000", r.get("errcode") == "0000", str(r.get("errcode")))
    check("识别出 1 张 invoice", n == 1 and types.get("invoice") == 1, f"{n} 条 {types}")
    b0 = (r["data"][0]["header"]["basic"] if n else {})
    check("invoiceNumber 正确", b0.get("invoiceNumber") == "INV2507-293", str(b0.get("invoiceNumber")))
    check("totalAmount 正确", str(b0.get("totalAmount")) in ("5286.12", "5286.1"), str(b0.get("totalAmount")))
    brn = (r["data"][0]["header"].get("billFrom") or {}).get("billFromBusinessRegistrationNumber") if n else None
    check("BRN 括号旧号完整", brn == "201801036457 (1298487-V)", str(brn))
    print(f"     耗时 {dt:.1f}s")

    print("\n═══ 3. A0704 / 4009 全 other 判失败 ═══")
    memo = HERE / "gray_memo_no_invoice.pdf"
    if not memo.exists():
        import fitz
        d = fitz.open(); p = d.new_page()
        p.insert_text((60, 100), "INTERNAL MEMO", fontsize=20)
        p.insert_text((60, 140), "Subject: office renovation schedule", fontsize=11)
        p.insert_text((60, 165), "The east wing closes from 3 March to 17 March.", fontsize=11)
        d.save(str(memo)); d.close()
    r2 = upload("/ai/knowledge/nlpService/document/analyze", tok, cid, memo)
    check("同步返 4009", r2.get("errcode") == "4009", f"errcode={r2.get('errcode')}")
    check("不返空壳数据", not (r2.get("data") or []), f"data={len(r2.get('data') or [])} 条")

    print("\n═══ 4. 异步全链路（提交→轮询→取结果）═══")
    sub = upload("/ai/knowledge/nlpService/document/analyze/async", tok, cid,
                 HERE / "S3_credit_note.pdf")
    tid = (sub.get("data") or {}).get("taskId")
    check("申请返 taskId", bool(tid), str(tid))
    check("申请响应无 traceId/docPages", "traceId" not in sub and "docPages" not in sub)
    t0 = time.time()
    entry, status = None, None
    while time.time() - t0 < 600:
        time.sleep(3)
        q = post_json(f"/ai/knowledge/nlpService/tasks/query?access_token={tok}",
                      {"taskIds": [tid]})
        entry = (q.get("data") or {}).get(tid) or {}
        status = entry.get("status")
        if status in ("SUCCESS", "FAILED", "COMPLETED"):
            break
    check("任务到终态", status in ("SUCCESS", "COMPLETED"), f"status={status} 用时 {time.time()-t0:.0f}s")
    res = entry.get("result") if entry else None
    check("result 是字符串（文档契约）", isinstance(res, str), type(res).__name__)
    if isinstance(res, str):
        payload = json.loads(res)
        cn = payload["data"][0]["header"]["basic"]
        check("Credit Note 负号保留", str(cn.get("totalAmount")) == "-709.25", str(cn.get("totalAmount")))

    print("\n═══ 5. 25 页大文档（准入闸页数维度）═══")
    big = pathlib.Path.home() / "Downloads" / "July Claim 20260826整个文件不能识别.pdf"
    if big.exists():
        t0 = time.time()
        r3 = upload("/ai/knowledge/nlpService/document/analyze", tok, cid, big)
        n3, types3, _ = summarize(r3)
        check("25 页识别成功", r3.get("errcode") == "0000", f"errcode={r3.get('errcode')}")
        check("多票据全部输出", n3 >= 20, f"{n3} 条 {types3}")
        print(f"     耗时 {time.time()-t0:.0f}s")
    else:
        print("  （跳过：本地无该样本）")

    ok = sum(1 for _, o, _ in results if o)
    print(f"\n═══ 灰度结论：{ok}/{len(results)} 项通过 ═══")
    if ok < len(results):
        print("失败项：", [n for n, o, _ in results if not o])


if __name__ == "__main__":
    main()
