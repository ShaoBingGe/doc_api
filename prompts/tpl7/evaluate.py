"""template 7 prompt 版本评测器。

对同一份 25 页报销贴单（July Claim）跑一次识别，按 8 条判据打分：
6 条是待修的问题（P1–P6），2 条是**已经正确、不许改坏**的行为（K1–K2）。

判据全部来自人工核对过的票面事实，不是模型自评：
  P1 第 2、3 页是 Shell 加油刷卡小票 → 必须 receipt（票面虽印 INVOICE，
     但无买方、无税额拆分，属小额支付凭证）
  P2 nameOfInvoice 是票面抬头，票面有就要输出
  P3 加油小票的单号取 Reference No —— 此前取了 Terminal（8 位数字），
     导致第 2 页 124.82 与第 3 页 121.2 出现同号 84202913
  P4 第 6 页 Agoda：GRAND TOTAL USD 54.98，但 Total Charge MYR 224.97
     实际扣款是 MYR → 马来西亚场景优先取 MYR
  P5 明细行 unitPrice 票面有就要填
  （原「国家/国家代码」一条已**撤出**：v1 正文 §1.4 与 Part 3 §3.7 明确
    「无法找到的字段一律不输出，不要推断、不要捏造」，从地址反推国家与该
    原则冲突。经确认尊重原则，不做国家推导。）

  K1 第 1 页（旋转 90°的银行卡交易行）判 other —— 正确，不许改
  K2 第 13–25 页（Touch'nGo 对账单）判 other —— 正确，不许改

**必须多次运行取通过率**：实测同一 prompt、同一模型连跑两次，
nameOfInvoice 一次 0/25 全空、一次 18/23 有值。单次结果分不清
「prompt 改好了」与「模型这次手气好」，据此调 prompt 就是在追鬼。

用法（在服务器上跑）：
    python evaluate.py <version.json>            # 跑 3 轮取通过率
    python evaluate.py <version.json> --runs 5   # 指定轮数
    python evaluate.py <version.json> --raw      # 额外落盘最后一轮原始响应
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/docapi/backend")

PDF = "/tmp/claim25.pdf"
FUEL_PAGES = {"2", "3"}          # Shell 加油刷卡小票所在页
AGODA_PAGE = "6"
AGODA_MYR = 224.97
TAIL_PAGES = {str(i) for i in range(13, 26)}


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _basic(e):
    return (e.get("header") or {}).get("basic", {})


def _lines(e):
    return (e.get("detail") or {}).get("detailOfGoodsOrServices") or []


def score(payload: dict) -> dict:
    ents = payload.get("data") or []
    real = [e for e in ents if _basic(e).get("docType") != "other"]
    fuel = [e for e in ents
            if set(_basic(e).get("page") or []) & FUEL_PAGES
            and _basic(e).get("docType") != "other"]

    # ── P1 加油小票必须判 receipt ────────────────────────────────────────
    p1_ok = sum(1 for e in fuel if _basic(e).get("docType") == "receipt")
    p1 = {"pass": bool(fuel) and p1_ok == len(fuel),
          "detail": f"{p1_ok}/{len(fuel)} 判为 receipt"}

    # ── P2 nameOfInvoice 填充率 ──────────────────────────────────────────
    named = sum(1 for e in real if str(_basic(e).get("nameOfInvoice") or "").strip())
    p2 = {"pass": bool(real) and named / len(real) >= 0.6,
          "detail": f"{named}/{len(real)} 有票面抬头"}

    # ── P3 加油小票单号：不得是 8 位纯数字（Terminal），且不得重号 ─────────
    nums = [str(_basic(e).get("invoiceNumber") or "").strip() for e in fuel]
    terminal_like = sum(1 for n in nums if n.isdigit() and len(n) == 8)
    dup = len(nums) - len(set(n for n in nums if n))
    filled = sum(1 for n in nums if n)
    p3 = {"pass": bool(fuel) and terminal_like == 0 and dup == 0 and filled == len(fuel),
          "detail": f"疑似 Terminal 号 {terminal_like} 个、重号 {dup} 个、"
                    f"已填 {filled}/{len(fuel)}"}

    # ── P4 Agoda 取 MYR ─────────────────────────────────────────────────
    ag = next((e for e in ents if AGODA_PAGE in (_basic(e).get("page") or [])
               and _basic(e).get("docType") != "other"), None)
    if ag is None:
        p4 = {"pass": False, "detail": "第 6 页未识别出票据"}
    else:
        b = _basic(ag)
        amt = _num(b.get("totalAmount"))
        ok = (b.get("currency") == "MYR" and amt is not None
              and abs(amt - AGODA_MYR) < 0.02)
        p4 = {"pass": ok,
              "detail": f"currency={b.get('currency')} totalAmount={b.get('totalAmount')}"
                        f"（应为 MYR / {AGODA_MYR}）"}

    # ── P5 明细行 unitPrice 填充率 ───────────────────────────────────────
    all_lines = [ln for e in real for ln in _lines(e)]
    up = sum(1 for ln in all_lines if _num(ln.get("unitPrice")) is not None)
    p5 = {"pass": bool(all_lines) and up / len(all_lines) >= 0.7,
          "detail": f"{up}/{len(all_lines)} 明细行有 unitPrice"}

    # ── P6 手写现金收据本（第 4、10 页的 CASH BILL）判 receipt ────────────
    # v4 引入的回退：A-2 反向边界让模型对 receipt 过度保守，把手写收据
    # 推成了 invoice。这类单据无税号、无买方、无税额拆分，只能是 receipt。
    cash = [e for e in ents
            if "CASH BILL" in str(_basic(e).get("nameOfInvoice") or "").upper()]
    cash_ok = sum(1 for e in cash if _basic(e).get("docType") == "receipt")
    p6 = {"pass": bool(cash) and cash_ok == len(cash),
          "detail": f"{cash_ok}/{len(cash)} 张 CASH BILL 判为 receipt"}

    # ── K1 / K2 不许改坏 ────────────────────────────────────────────────
    first = next((e for e in ents if _basic(e).get("page") == ["1"]), None)
    k1 = {"pass": first is not None and _basic(first).get("docType") == "other",
          "detail": f"第 1 页 docType="
                    f"{_basic(first).get('docType') if first else '(缺失)'}"}

    tail = [e for e in ents if set(_basic(e).get("page") or []) & TAIL_PAGES]
    tail_other = all(_basic(e).get("docType") == "other" for e in tail)
    k2 = {"pass": bool(tail) and tail_other,
          "detail": f"13–25 页 {len(tail)} 条，全为 other={tail_other}"}

    checks = {"P1 加油票判receipt": p1, "P2 nameOfInvoice": p2,
              "P3 加油票取RefNo": p3, "P4 Agoda取MYR": p4,
              "P5 unitPrice": p5, "P6 现金收据判receipt": p6,
              "K1 第1页other": k1, "K2 尾页other": k2}
    passed = sum(1 for c in checks.values() if c["pass"])
    return {"checks": checks, "passed": passed, "total": len(checks),
            "entities": len(ents), "real": len(real), "lines": len(all_lines)}


def run(version_file: str, save_raw: bool = False) -> dict:
    from app.processors.factory import ProcessorFactory

    cfg = json.loads(Path(version_file).read_text())
    prompt, schema = cfg["composed_prompt"], cfg["composed_schema"]
    from app.core.config import get_settings
    proc, model = ProcessorFactory.resolve_spec(
        "gemini", get_settings().GEMINI_MODEL)
    p = ProcessorFactory.create(proc, model_name=model)

    t0 = time.time()
    raw = p.process_document(PDF, prompt, {"response_schema": schema})
    sec = time.time() - t0

    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]

    from app.services.open_api_mapper import build_response
    payload = build_response(data, trace_id="evalrun00000000", doc_pages=25)

    if save_raw:
        out = Path(version_file).with_suffix(".result.json")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    res = score(payload)
    res["seconds"] = round(sec, 1)
    res["model"] = f"{proc}/{model}"
    return res


def run_many(version_file: str, runs: int = 3, save_raw: bool = False) -> dict:
    """跑 N 轮，按判据统计通过率。模型有抖动，单轮不足以判断。"""
    results = []
    for i in range(runs):
        try:
            results.append(run(version_file, save_raw=(save_raw and i == runs - 1)))
        except Exception as exc:  # noqa: BLE001 — 一轮炸了不该毁掉整次评测
            print(f"  第 {i + 1} 轮失败：{type(exc).__name__}: {str(exc)[:90]}")
        if i < runs - 1:
            time.sleep(3)
    if not results:
        return {"runs": 0}

    keys = list(results[0]["checks"])
    agg = {}
    for k in keys:
        ok = sum(1 for r in results if r["checks"][k]["pass"])
        agg[k] = {"ok": ok, "n": len(results),
                  "details": [r["checks"][k]["detail"] for r in results]}
    return {
        "runs": len(results), "agg": agg,
        "scores": [r["passed"] for r in results],
        "seconds": [r["seconds"] for r in results],
        "model": results[0]["model"],
        "entities": [r["entities"] for r in results],
    }


def report(name: str, m: dict) -> str:
    if not m.get("runs"):
        return f"### {name}　全部轮次失败"
    n = m["runs"]
    lines = [
        f"### {name}　{n} 轮：得分 {m['scores']}"
        f"　均值 {sum(m['scores']) / n:.1f}/8　（{m['seconds']}s · {m['model']}）",
        f"实体数 {m['entities']}", "",
    ]
    for k, a in m["agg"].items():
        mark = "✅" if a["ok"] == n else ("⚠️" if a["ok"] else "❌")
        lines.append(f"  {mark} {k}　{a['ok']}/{n} 轮通过")
        for d in a["details"]:
            lines.append(f"       · {d}")
    return "\n".join(lines)


if __name__ == "__main__":
    vf = sys.argv[1]
    n = 3
    if "--runs" in sys.argv:
        n = int(sys.argv[sys.argv.index("--runs") + 1])
    m = run_many(vf, runs=n, save_raw="--raw" in sys.argv)
    print(report(Path(vf).stem, m))
