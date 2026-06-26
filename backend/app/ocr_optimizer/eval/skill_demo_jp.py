"""Real end-to-end skill-optimization demo on Japan-inv (dev tool, NOT prod path).

Runs the disciplined ReflACT loop (skilltrain.driver) with REAL OCR (extract) +
REAL qwen reflection, on a JP init train/val split, to validate that the loop
actually lifts weak fields on a HELD-OUT set — and that the gate refuses
non-generalizing edits.

Run manually on the server (issues real VLM/LLM calls):
    cd backend && .venv/bin/python -m app.ocr_optimizer.eval.skill_demo_jp

First validated run (init train6/val2, 2 rounds, targets =
invoiceDate/billFromName/billFromTaxIdentificationNumber):
    billFromTaxIdentificationNumber  val 0.00 -> 1.00
    billFromName                     val 0.00 -> 0.50
    invoiceDate                      val 0.00 -> 0.00   (not cracked in 2 rounds)
    history: round1 ACCEPT (0.0->0.5), round2 REJECT (held) — gate discipline OK.
"""
from __future__ import annotations

import hashlib
from statistics import mean

TARGETS = ["invoiceDate", "billFromName", "billFromTaxIdentificationNumber"]


def _build():
    from app.ocr_optimizer.eval import bench_japan_inv as b
    from app.ocr_optimizer.service.llm_call import llm_text_completion
    from app.ocr_optimizer.skilltrain.types import FieldEdit
    from app.processors.factory import ProcessorFactory

    base_prompt, schema = b.build_country_prompt("JP")
    proc = ProcessorFactory.create("qwen")
    cache: dict = {}

    def extract(pdf, skill_doc):
        key = (str(pdf), hashlib.md5((skill_doc or "").encode()).hexdigest())
        if key in cache:
            return cache[key]
        full = base_prompt + (
            ("\n\n# 技能补充规则（最高优先级）\n" + skill_doc) if (skill_doc or "").strip() else ""
        )
        out = b._parse_entity(proc.process_document(str(pdf), full, {"schema": schema}))
        cache[key] = out
        return out

    def reflect(field, examples):
        ex = "\n".join(f"- 正确值={e['gt']!r}  模型输出={e['pred']!r}" for e in examples[:6])
        sysmsg = ("你是 OCR 字段规则优化专家。依据错误样本，为该字段产出一条简短、可泛化的"
                  "取值/格式规整规则。只输出 JSON：{\"rule\":\"...\"}。")
        usr = f"字段：{field}\n错误样本：\n{ex}\n给出一条让模型下次正确的规则（中文，一句话）。"
        try:
            out = llm_text_completion(processor_spec="qwen", model_name=None,
                                      system_instruction=sysmsg, user_prompt=usr, as_json=True)
            rule = out.get("rule") if isinstance(out, dict) else None
        except Exception:
            rule = None
        return [FieldEdit(op="append", target=field, content=rule)] if rule else []

    return b, extract, reflect


def main(max_rounds: int = 2, l_max: int = 3) -> None:
    from app.ocr_optimizer.skilltrain import driver

    b, extract, reflect = _build()
    allp = b.load("init")
    train, val = allp[:6], allp[6:8]

    def field_acc(doc, pairs):
        rows = [b.score_pred(extract(pdf, doc), gt, TARGETS) for pdf, gt in pairs]
        return {f: mean(1.0 if r["per_field"][f]["hard"] else 0.0 for r in rows) for f in TARGETS}

    before = field_acc("", val)
    res = driver.optimize_skill(
        skill_doc="", extract_fn=extract, reflect_fn=reflect,
        train_pairs=train, val_pairs=val, fields=TARGETS, max_rounds=max_rounds, l_max=l_max,
    )
    after = field_acc(res.best_doc, val)
    print("history:", [(h.round, h.action, h.val_score) for h in res.history])
    for f in TARGETS:
        print(f"  {f:34} val {before[f]:.2f} -> {after[f]:.2f}")
    print("best_skill_doc:\n" + (res.best_doc or "(empty)"))


if __name__ == "__main__":
    main()
