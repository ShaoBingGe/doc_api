"""中文版 vs 英文翻译版 MY 7_16 模板 —— A/B 对比（6 页测试 + 5 份随机黄金样本）。

背景：用户请 Gemini 把现行 MY_invoice_prompt.yaml（中文正文）逐条翻译成英文，
规则本身未改动，据称能省 token、提准确率（论点：英文票据 + 英文提示词，模型
不必做「中文指令 → 英文票面」的语间映射，注意力更集中）。

方法论与 run_ab.py / paired_golden.py 一致——直调 GeminiProcessor（生产同款
调用类），两版模板各自走 decompose + build_v1_prompt 完整组装，同一批文档跑
一遍，逐样本记录：识别耗时、token 消耗（prompt/output/total）、准确率。

样本 = S2_six_invoices.pdf（6 页 6 张独立发票，考切分）+ 黄金池随机 5 份
（覆盖核心字段集，用生产同款 module 打分，strict 模式）。

用法：cd backend && .venv/bin/python eval_samples/schema_ab/translation_ab.py
"""

from __future__ import annotations

import json
import pathlib
import random
import re
import sys
import tempfile
import time
import uuid as uuidlib

sys.path.insert(0, ".")

from app.core.database import SessionLocal                     # noqa: E402
from app.models.api_definition import ApiDefinition            # noqa: E402
from app.ocr_optimizer.eval import golden_loop                 # noqa: E402
from app.ocr_optimizer.eval.harness import (                   # noqa: E402
    module_specs_from_orm,
    score_outputs,
)
from app.ocr_optimizer.models import (                         # noqa: E402
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
)
from app.processors.gemini_processor import GeminiProcessor    # noqa: E402

HERE = pathlib.Path(__file__).parent
REPO_ROOT = HERE.parent.parent.parent
GOLDEN_DIR = HERE.parent.parent / "app" / "ocr_optimizer" / "eval" / "golden_set" / "MY"

CANDIDATE_A = REPO_ROOT / "MY_invoice_prompt.yaml"                             # 现行中文版
CANDIDATE_B = pathlib.Path.home() / "Downloads" / "gemini-code-1788175783687.yaml"  # 英文翻译版
S2 = HERE / "S2_six_invoices.pdf"
S2_GT = json.loads((HERE / "ground_truth.json").read_text())["S2_six_invoices.pdf"]


_KEY_RE = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_]*):(\s+)(.+)$')
_BLOCK_INDICATORS = {"|", "|-", "|+", ">", ">-", ">+"}
_ALREADY_SAFE_PREFIXES = ("'", '"', "|", ">", "[", "{", "&", "*")


def _fix_unquoted_scalars(text: str) -> str:
    """候选 yaml 是逐行手写的英文翻译，大量 `key: value` 单行纯量内部含
    ASCII `: `（如 "look for: 'Account Name'..."），这会让 YAML 解析器把
    冒号后的内容误判成新的 mapping value（现行中文版正文的等价说明用全角
    `：`，从未撞到这个坑）。规则本身不受影响——这里只是把每一行未加引号的
    纯量安全地补上引号（json.dumps 转义），不改变解析出的字符串内容；
    已用 `|-`/`>-` 块标量或已加引号的行原样跳过，块标量体内的续行同样跳过。"""
    out: list[str] = []
    block_indent: int | None = None
    for raw_ln in text.splitlines():
        if block_indent is not None:
            indent = len(raw_ln) - len(raw_ln.lstrip(" "))
            if raw_ln.strip() == "" or indent > block_indent:
                out.append(raw_ln)
                continue
            block_indent = None  # 块标量结束，本行按普通行继续处理

        m = _KEY_RE.match(raw_ln)
        if not m:
            out.append(raw_ln)
            continue
        indent, key, sep, value = m.groups()
        value = value.rstrip()
        if value in _BLOCK_INDICATORS:
            block_indent = len(indent)
            out.append(raw_ln)
            continue
        if value.startswith(_ALREADY_SAFE_PREFIXES):
            out.append(raw_ln)
            continue
        out.append(f"{indent}{key}:{sep}{json.dumps(value, ensure_ascii=False)}")
    return "\n".join(out) + "\n"


def load_candidate_yaml(path: pathlib.Path) -> tuple[str, dict]:
    """候选 yaml → (组装后 prompt, json_schema)，走与生产 seed 相同的组装路径。"""
    from app.ocr_optimizer.service import template_loader
    from app.ocr_optimizer.service.preset_init import build_v1_prompt

    with tempfile.TemporaryDirectory() as td:
        fixed = _fix_unquoted_scalars(path.read_text(encoding="utf-8"))
        (pathlib.Path(td) / "MY_invoice_prompt.yaml").write_text(fixed, encoding="utf-8")
        orig = template_loader._REPO_ROOT
        template_loader._REPO_ROOT = pathlib.Path(td)
        try:
            d = template_loader.decompose_country_template("MY")
        finally:
            template_loader._REPO_ROOT = orig
    return build_v1_prompt(d), d["json_schema"]


def parse(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        from app.processors.base import extract_json
        blocks = extract_json(raw or "")
        if blocks:
            try:
                return json.loads(blocks[0])
            except (json.JSONDecodeError, TypeError):
                pass
        return {"_raw": (raw or "")[:200]}


def score_s2(out) -> tuple[int, int, list[str]]:
    ents = out if isinstance(out, list) else []
    basics = [e for e in ents if isinstance(e, dict)]
    docTypes = [b.get("docType") for b in basics]
    invoiceTypes = [b.get("invoiceType") for b in basics if b.get("docType") != "other"]
    invoiceNumbers = [b.get("invoiceNumber") for b in basics if b.get("docType") != "other"]
    checks = [
        ("n=6", len(basics) == S2_GT["n"], str(len(basics))),
        ("docTypes", docTypes == S2_GT["docTypes"], str(docTypes)),
        ("invoiceTypes", invoiceTypes == S2_GT["invoiceTypes"], str(invoiceTypes)),
        ("invoiceNumbers", invoiceNumbers == S2_GT["invoiceNumbers"], str(invoiceNumbers)),
    ]
    fails = [f"{n}: {got}" for n, ok, got in checks if not ok]
    return sum(1 for _, ok, _ in checks if ok), len(checks), fails


def call(proc: GeminiProcessor, path: str, prompt: str, schema: dict) -> dict:
    t0 = time.time()
    raw = proc.process_document(path, prompt, {"response_schema": schema})
    elapsed = time.time() - t0
    usage = dict(proc.last_usage or {})
    return {"elapsed_s": elapsed, "usage": usage, "output": parse(raw)}


def main() -> None:
    if not CANDIDATE_B.exists():
        raise SystemExit(f"找不到候选文件：{CANDIDATE_B}")

    random.seed()
    db = SessionLocal()
    api = db.query(ApiDefinition).filter_by(api_code="my-invoice-c99a19").first()
    v = (db.query(OcrPromptVersion)
         .filter_by(api_definition_id=api.id, status=PromptVersionStatus.active.value)
         .first())
    mods = (db.query(OcrModule).filter_by(prompt_version_id=v.id)
            .order_by(OcrModule.order_index).all())
    specs = module_specs_from_orm(mods)

    golden = golden_loop.load_golden("MY")
    core = set(golden_loop.compute_core_fields("MY"))
    pool = [d for d, e in golden.items() if core <= golden_loop._top_keys(e["gt"])]
    picks = random.sample(pool, 5)
    print(f"随机抽中的 5 份黄金样本: {[p[:8] for p in picks]}\n")

    variants = {
        "A_中文现行": load_candidate_yaml(CANDIDATE_A),
        "B_英文翻译": load_candidate_yaml(CANDIDATE_B),
    }
    for name, (prompt, _schema) in variants.items():
        print(f"{name}: prompt {len(prompt):,} 字符")
    print()

    proc = GeminiProcessor()
    all_results: dict[str, dict] = {}

    # ── S2 六联发票 ──
    print("═══ S2_six_invoices.pdf（6 页 6 张独立发票）═══")
    for name, (prompt, schema) in variants.items():
        r = call(proc, str(S2), prompt, schema)
        p, total, fails = score_s2(r["output"])
        r["score"] = f"{p}/{total}"
        all_results[f"S2|{name}"] = r
        u = r["usage"]
        print(f"  {name}: {p}/{total}  耗时 {r['elapsed_s']:.1f}s  "
              f"token(prompt/output/total)={u.get('prompt_tokens')}/"
              f"{u.get('output_tokens')}/{u.get('total_tokens')}"
              + (f"  ← {'; '.join(fails)}" if fails else ""))
    print()

    # ── 5 份随机黄金样本 ──
    print("═══ 5 份随机黄金样本（strict 逐字段打分）═══")
    outputs_by_variant: dict[str, dict[str, dict]] = {n: {} for n in variants}
    gts = {str(uuidlib.UUID(d)): golden[d]["gt"] for d in picks}
    for d in picks:
        doc_path = GOLDEN_DIR / golden[d]["doc"]
        did = str(uuidlib.UUID(d))
        print(f" · {d[:8]} ({golden[d]['doc']})")
        for name, (prompt, schema) in variants.items():
            r = call(proc, str(doc_path), prompt, schema)
            all_results[f"{d[:8]}|{name}"] = r
            outputs_by_variant[name][did] = r["output"]
            u = r["usage"]
            print(f"    {name}: 耗时 {r['elapsed_s']:.1f}s  "
                  f"token={u.get('prompt_tokens')}/{u.get('output_tokens')}/{u.get('total_tokens')}")

    print("\n═══ 黄金样本 strict 准确率（生产同款 module，逐字段精确匹配） ═══")
    reports = {}
    for name in variants:
        report = score_outputs(specs, outputs_by_variant[name], gts, strict=True)
        reports[name] = report
        print(f"  {name}: overall={report.overall_accuracy:.4f}  "
              f"（{report.module_count} 个字段有效判据，"
              f"{len(report.ocr_error_doc_ids)} 份传输失败）")

    a_acc = {m.module_key: m.accuracy for m in reports["A_中文现行"].module_scores}
    b_acc = {m.module_key: m.accuracy for m in reports["B_英文翻译"].module_scores}
    regress = [k for k in a_acc if k in b_acc and b_acc[k] < a_acc[k] - 1e-9]
    improve = [k for k in b_acc if k in a_acc and b_acc[k] > a_acc[k] + 1e-9]
    if regress:
        print(f"  英文版退化字段: {regress}")
    if improve:
        print(f"  英文版提升字段: {improve}")

    # ── 汇总：耗时 / token ──
    def agg(prefix_names):
        rows = [all_results[k] for k in all_results if k.split("|")[1] in prefix_names]
        return rows

    print("\n═══ 汇总（全部 6 份样本，各版本合计）═══")
    for name in variants:
        rows = [v for k, v in all_results.items() if k.endswith(f"|{name}")]
        tot_time = sum(r["elapsed_s"] for r in rows)
        tot_prompt_tok = sum((r["usage"].get("prompt_tokens") or 0) for r in rows)
        tot_out_tok = sum((r["usage"].get("output_tokens") or 0) for r in rows)
        print(f"  {name}: 总耗时 {tot_time:.1f}s（均 {tot_time/len(rows):.1f}s/份）  "
              f"总 prompt token {tot_prompt_tok}（均 {tot_prompt_tok/len(rows):.0f}/份）  "
              f"总 output token {tot_out_tok}（均 {tot_out_tok/len(rows):.0f}/份）")

    dump = {k: {"elapsed_s": r["elapsed_s"], "usage": r["usage"],
                "score": r.get("score"), "output": r["output"]}
            for k, r in all_results.items()}
    (HERE / "translation_ab_results.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=1))
    print(f"\n→ 明细已存 {HERE / 'translation_ab_results.json'}")


if __name__ == "__main__":
    main()
