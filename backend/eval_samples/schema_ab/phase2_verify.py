"""Phase 2：三样本走**完整生产调用链**验证 PASS_SCHEMA_TO_MODEL。

与 Phase 0 的 run_ab.py 不同——那个直调 GeminiProcessor 只验模型行为；
这里经 `/ai/knowledge/nlpService/document/analyze` 走完整栈：
MD5 鉴权 → 准入闸 → resolve_chain → extract_document（取 active 版本
prompt+schema）→ _run_with_failover → mapper 嵌套映射。验的是**管道**。

结果缓存必须关：同一文件重复提交会被 15 分钟缓存短路，测出来的是上一轮
的答案（2026-08-27 已被这个坑骗过一次）。

用法：cd backend && .venv/bin/python eval_samples/schema_ab/phase2_verify.py [--rounds 3]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import sys
import time
import uuid

sys.path.insert(0, ".")

from fastapi.testclient import TestClient          # noqa: E402

from app.core.config import get_settings           # noqa: E402
from app.core.database import SessionLocal         # noqa: E402
from app.main import app                           # noqa: E402
from app.models.open_api_client import OpenApiClient  # noqa: E402

HERE = pathlib.Path(__file__).parent
GT = json.loads((HERE / "ground_truth.json").read_text())
SAMPLES = ["S1_single_invoice.pdf", "S2_six_invoices.pdf", "S3_credit_note.pdf"]


def token(client: TestClient, cid: str, secret: str) -> str:
    ts = str(int(time.time()))
    sign = hashlib.md5(f"{cid}{secret}{ts}".encode()).hexdigest()
    r = client.post("/base/oauth/token",
                    json={"client_id": cid, "timestamp": ts, "sign": sign})
    return r.json()["access_token"]


def analyze(client: TestClient, tok: str, cid: str, path: pathlib.Path) -> dict:
    return client.post(
        f"/ai/knowledge/nlpService/document/analyze?access_token={tok}",
        data={"templateId": "7", "clientId": cid,
              "fileHash": f"p2-{uuid.uuid4().hex[:10]}"},
        files={"file": (path.name, io.BytesIO(path.read_bytes()),
                        "application/pdf")},
    ).json()


def observe(payload: dict) -> dict:
    ents = payload.get("data") or []
    basics = [(e.get("header") or {}).get("basic") or {} for e in ents]
    billing = [b for b in basics if b.get("docType") != "other"]
    return {
        "errcode": payload.get("errcode"),
        "n": len(ents),
        "docTypes": [b.get("docType") for b in basics],
        "invoiceTypes": [b.get("invoiceType") for b in billing],
        "invoiceNumbers": [b.get("invoiceNumber") for b in billing],
        "totals": [b.get("totalAmount") for b in billing],
        "brn": [((e.get("header") or {}).get("billFrom") or {})
                .get("billFromBusinessRegistrationNumber")
                for e in ents
                if ((e.get("header") or {}).get("basic") or {}).get("docType") != "other"],
        "origRefs": [len((e.get("detail") or {}).get("originalInvoiceReferences") or [])
                     for e in ents],
        "lineRows": [len((e.get("detail") or {}).get("detailOfGoodsOrServices") or [])
                     for e in ents],
    }


def num_eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


def score(sample: str, obs: dict) -> tuple[int, int, list[str]]:
    """→ (通过数, 总判据数, 失败明细)。判据取自 ground_truth.json。"""
    gt = GT[sample]
    checks: list[tuple[str, bool, str]] = []

    checks.append(("errcode=0000", obs["errcode"] == "0000", str(obs["errcode"])))
    checks.append((f"条数={gt['n']}", obs["n"] == gt["n"], str(obs["n"])))
    checks.append(("docTypes", obs["docTypes"] == gt["docTypes"],
                   str(obs["docTypes"])))
    checks.append(("invoiceTypes", obs["invoiceTypes"] == gt["invoiceTypes"],
                   str(obs["invoiceTypes"])))
    checks.append(("invoiceNumbers", obs["invoiceNumbers"] == gt["invoiceNumbers"],
                   str(obs["invoiceNumbers"])))
    if "totals" in gt:
        ok = (len(obs["totals"]) == len(gt["totals"])
              and all(num_eq(a, b) for a, b in zip(obs["totals"], gt["totals"])))
        checks.append(("totals", ok, str(obs["totals"])))
    if "brn" in gt:
        checks.append(("brn", obs["brn"] == gt["brn"], str(obs["brn"])))
    if "lineRows" in gt:
        checks.append(("lineRows", obs["lineRows"] == gt["lineRows"],
                       str(obs["lineRows"])))
    if "origRefs" in gt:
        ok = obs["origRefs"] and obs["origRefs"][0] >= gt["origRefs"][0]
        checks.append(("origRefs≥1", bool(ok), str(obs["origRefs"])))

    fails = [f"{name}: {got}" for name, ok, got in checks if not ok]
    return sum(1 for _, ok, _ in checks if ok), len(checks), fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    s = get_settings()
    s.EXTRACT_CACHE_ENABLED = False   # 必须——否则测的是上一轮缓存

    db = SessionLocal()
    c = db.query(OpenApiClient).first()
    cid, secret = c.client_id, c.client_secret
    db.close()

    client = TestClient(app)
    tok = token(client, cid, secret)
    print(f"client={cid} · rounds={args.rounds} · 缓存已关\n")

    tally: dict[str, dict[str, list[int]]] = {}
    for sample in SAMPLES:
        tally[sample] = {}
        for mode, flag in (("关", False), ("开", True)):
            s.PASS_SCHEMA_TO_MODEL = flag
            passes = []
            for r in range(args.rounds):
                obs = observe(analyze(client, tok, cid, HERE / sample))
                p, total, fails = score(sample, obs)
                passes.append(p)
                mark = "✅" if p == total else "❌"
                print(f"  {mark} {sample[:2]} 开关{mode} r{r}: {p}/{total}"
                      + (f"  ← {'; '.join(fails)}" if fails else ""))
            tally[sample][mode] = passes
        print()

    s.PASS_SCHEMA_TO_MODEL = False
    print("═══ 汇总（每格 = 各轮通过判据数）═══")
    verdict_ok = True
    for sample in SAMPLES:
        off, on = tally[sample]["关"], tally[sample]["开"]
        cmp = "开=关" if sum(on) == sum(off) else ("开>关" if sum(on) > sum(off) else "开<关 ⚠️")
        if sum(on) < sum(off):
            verdict_ok = False
        print(f"  {sample:26} 关={off} 开={on}   {cmp}")
    print("\n结论：", "通过（开不劣于关）" if verdict_ok else "未通过——有字段在开启后退化")
    (HERE / "phase2_results.json").write_text(
        json.dumps(tally, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
