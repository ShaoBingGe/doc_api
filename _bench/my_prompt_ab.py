"""MY prompt A/B bench — 精简版 vs 原版，在黄金集上做 GT 锚定的逐字段对比。

用法:
    python _bench/my_prompt_ab.py run   <variant.yaml> --model qwen3-vl-plus -n 15 --tag base
    python _bench/my_prompt_ab.py score <tag-a> <tag-b>

设计要点（保证 A/B 公平）:
  * 同一批文档、同一 GT、同一打分器，只换 prompt 变体。
  * GT 来自多个客户 API，字段命名不统一 → 用别名合并；税号类标签不可信
    （salerTIN 里装的是 BRN）→ 按值的格式路由，不按标签。
  * 只对 GT 里真实存在的值计分；GT 缺失的字段不计入（不会把「没考的题」算成
    满分或零分）。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import pathlib
import random
import re
import sys

import fitz  # PyMuPDF
import httpx
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLD = ROOT / "backend/app/ocr_optimizer/eval/golden_set/MY"
OUT = ROOT / "_bench/results"
OUT.mkdir(parents=True, exist_ok=True)

MAX_PAGES = 5
RENDER_DPI = 150
TIMEOUT = 420.0

TAX_CATEGORIES = (
    "VAT (Value Added Tax), GST (Goods and Services Tax), SST (Sales and Service Tax), "
    "CIT (Corporate Income Tax), PIT (Personal Income Tax), WHT (Withholding Tax), "
    "CT (Consumption Tax), ST (Sales Tax), SVT (Service Tax), EXC (Excise Tax)"
)


# ── env ──────────────────────────────────────────────────────────────────────

def load_env() -> dict:
    env = {}
    for line in (ROOT / "backend/.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()


# ── prompt rendering ─────────────────────────────────────────────────────────

def render(variant_path: str) -> tuple[str, dict]:
    d = yaml.safe_load(pathlib.Path(variant_path).read_text())
    pt = d["prompt_template"]
    prompt = pt["prompt_format"].replace("{tax_categories_text}", TAX_CATEGORIES)
    return prompt, pt["json_schema"]


def to_gemini_schema(node):
    """json_schema (大写 TYPE / anyOf) → Gemini response_schema 形状。"""
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "type":
            out["type"] = str(v).upper()
        elif k in ("properties",):
            out[k] = {kk: to_gemini_schema(vv) for kk, vv in v.items()}
        elif k in ("items",):
            out[k] = to_gemini_schema(v)
        elif k == "anyOf":
            out[k] = [to_gemini_schema(x) for x in v]
        else:
            out[k] = v
    return out


# ── model calls ──────────────────────────────────────────────────────────────

def pdf_pages(path: pathlib.Path) -> list[bytes]:
    pages = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            if i >= MAX_PAGES:
                break
            pages.append(page.get_pixmap(dpi=RENDER_DPI).tobytes("png"))
    return pages


def call_qwen(model: str, prompt: str, schema: dict, pngs: list[bytes]) -> dict:
    """qwen 视觉模型不支持 response_schema → schema 必须进 prompt 正文。"""
    text = (
        prompt
        + "\n\n**Output JSON Schema (strictly follow; output a JSON array):**\n"
        + json.dumps(schema, ensure_ascii=False)
        + "\n\nOutput ONLY the JSON array, no markdown fences, no commentary."
    )
    content = [{"type": "text", "text": text}]
    for png in pngs:
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        content.append({"type": "image_url", "image_url": {"url": url}})
    resp = httpx.post(
        f"{ENV['DASHSCOPE_BASE_URL'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {ENV['QWEN_API_KEY']}"},
        json={"model": model, "messages": [{"role": "user", "content": content}],
              "temperature": 0},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    usage = body.get("usage") or {}
    return {
        "text": (body["choices"][0]["message"]["content"] or ""),
        "in_tokens": usage.get("prompt_tokens"),
        "out_tokens": usage.get("completion_tokens"),
    }


def call_gemini(model: str, prompt: str, schema: dict, pngs: list[bytes]) -> dict:
    """Gemini 走 response_schema 硬约束（schema 不进 prompt 正文）。"""
    parts = [{"text": prompt}]
    for png in pngs:
        parts.append({"inline_data": {"mime_type": "image/png",
                                      "data": base64.b64encode(png).decode()}})
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": ENV["GEMINI_API_KEY"],
                 "Content-Type": "application/json"},
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(schema),
            },
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    usage = body.get("usageMetadata") or {}
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        text = ""
    return {
        "text": text,
        "in_tokens": usage.get("promptTokenCount"),
        "out_tokens": usage.get("candidatesTokenCount"),
    }


def parse_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# ── run ──────────────────────────────────────────────────────────────────────

def pick_docs(n: int, seed: int) -> list[dict]:
    manifest = json.loads((GOLD / "manifest.json").read_text())
    items = manifest["items"]
    rnd = random.Random(seed)
    return items if n >= len(items) else rnd.sample(items, n)


def run(variant: str, model: str, n: int, tag: str, seed: int, workers: int) -> None:
    prompt, schema = render(variant)
    docs = pick_docs(n, seed)
    is_gemini = model.startswith("gemini")
    print(f"变体={variant}  模型={model}  样本={len(docs)}  seed={seed}")

    def one(item):
        doc = GOLD / item["doc"]
        try:
            pngs = pdf_pages(doc)
            fn = call_gemini if is_gemini else call_qwen
            # 瞬时网络错误重试——传输失败不该被记成模型答错（会污染 A/B）
            for attempt in range(3):
                try:
                    r = fn(model, prompt, schema, pngs)
                    break
                except (httpx.RemoteProtocolError, httpx.ReadTimeout,
                        httpx.ConnectError, httpx.HTTPStatusError):
                    if attempt == 2:
                        raise
                    import time
                    time.sleep(3 * (attempt + 1))
            parsed = parse_json(r["text"])
            return {
                "doc": item["doc"], "gt": item["ground_truth"],
                "parsed": parsed, "parse_ok": parsed is not None,
                "in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"],
                "raw_head": (r["text"] or "")[:300] if parsed is None else None,
            }
        except Exception as exc:  # noqa: BLE001 — 单样本失败不该中断整批
            return {"doc": item["doc"], "gt": item["ground_truth"],
                    "parsed": None, "parse_ok": False, "error": repr(exc)[:300]}

    results = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(one, docs), 1):
            flag = "ok " if r["parse_ok"] else "FAIL"
            print(f"  [{i:2d}/{len(docs)}] {flag} {pathlib.Path(r['doc']).name[:52]}"
                  f"  in={r.get('in_tokens')}")
            results.append(r)

    path = OUT / f"{tag}.json"
    path.write_text(json.dumps(
        {"variant": variant, "model": model, "seed": seed, "results": results},
        ensure_ascii=False, indent=1))
    ok = sum(1 for r in results if r["parse_ok"])
    print(f"\n写入 {path}  解析成功 {ok}/{len(results)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("variant")
    p.add_argument("--model", default="qwen3-vl-plus")
    p.add_argument("-n", type=int, default=15)
    p.add_argument("--tag", required=True)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.variant, a.model, a.n, a.tag, a.seed, a.workers)
    else:
        sys.exit(2)
