"""日本票据 prompt 变体 OCR runner — 直连 DashScope（qwen3-vl-plus）。

用法:
    python japan/_bench/run_jp.py run japan/_bench/variants/v0_baseline.yaml \
        --split dev --tag v0-dev [--model qwen3-vl-plus] [--workers 6]

设计:
  * 不走后端 QwenProcessor（其 _MAX_PAGES=5 会截断大文件）。
  * 页策略: ≤10 页整文件一次调用; >10 页取前 8 + 后 2（均为单发票大文件，
    关键信息在首/末页）。
  * 截断兜底: 输出 JSON 解析失败且疑似截断 → 页对半分两次调用合并,
    page 号按偏移校正（混贴小票不跨页, 风险可控）。
  * 缓存: results/<tag>/<doc>.json 存在且 prompt_sha/model 一致则跳过。
  * temperature=0, max_tokens 放大, 瞬时网络错误重试 3 次。
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import json
import pathlib
import re
import sys
import time

import fitz  # PyMuPDF
import httpx
import yaml

JAPAN = pathlib.Path(__file__).resolve().parents[1]
ROOT = JAPAN.parent
DOCS = JAPAN / "docs"
OUT = JAPAN / "_bench" / "results"

RENDER_DPI = int(__import__("os").environ.get("JP_DPI", "150"))
TIMEOUT = 420.0
MAX_TOKENS = 16384
FULL_PAGE_LIMIT = 10   # ≤10 页整文件单次调用
HEAD_PAGES, TAIL_PAGES = 8, 2  # >10 页取前 8 + 后 2

# 原模板 {tax_categories_text} 占位符的标准税种表（与 MY bench 一致，仅 v0 基线用）
TAX_CATEGORIES = (
    "VAT (Value Added Tax), GST (Goods and Services Tax), SST (Sales and Service Tax), "
    "CIT (Corporate Income Tax), PIT (Personal Income Tax), WHT (Withholding Tax), "
    "CT (Consumption Tax), ST (Sales Tax), SVT (Service Tax), EXC (Excise Tax)"
)


def load_env() -> dict:
    env = {}
    for line in (ROOT / "backend/.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()


# ── prompt / 渲染 ────────────────────────────────────────────────────────────

def render(variant_path: str) -> tuple[str, dict]:
    d = yaml.safe_load(pathlib.Path(variant_path).read_text())
    pt = d["prompt_template"]
    prompt = pt["prompt_format"].replace("{tax_categories_text}", TAX_CATEGORIES)
    return prompt, pt["json_schema"]


def page_selection(total: int) -> list[int]:
    """返回要渲染的 0-based 页索引。"""
    if total <= FULL_PAGE_LIMIT:
        return list(range(total))
    return list(range(HEAD_PAGES)) + list(range(total - TAIL_PAGES, total))


def render_doc(path: pathlib.Path) -> tuple[list[bytes], list[int]]:
    """→ (png bytes 列表, 对应的 1-based 真实页号列表)。图片视为单页。"""
    if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return [path.read_bytes()], [1]
    with fitz.open(path) as doc:
        idxs = page_selection(len(doc))
        pngs = [doc[i].get_pixmap(dpi=RENDER_DPI).tobytes("png") for i in idxs]
        return pngs, [i + 1 for i in idxs]


# ── 模型调用 ─────────────────────────────────────────────────────────────────

def call_qwen(model: str, prompt: str, schema: dict, pngs: list[bytes],
              suffix: str) -> dict:
    """qwen 视觉模型不支持 response_schema → schema 进 prompt 正文。"""
    text = (
        prompt
        + "\n\n**Output JSON Schema (strictly follow; output a JSON array):**\n"
        + json.dumps(schema, ensure_ascii=False)
        + "\n\nOutput ONLY the JSON array, no markdown fences, no commentary."
    )
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    content = [{"type": "text", "text": text}]
    for png in pngs:
        url = f"data:{mime};base64," + base64.b64encode(png).decode()
        content.append({"type": "image_url", "image_url": {"url": url}})
    resp = httpx.post(
        f"{ENV['DASHSCOPE_BASE_URL'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {ENV['QWEN_API_KEY']}"},
        json={"model": model, "messages": [{"role": "user", "content": content}],
              "temperature": 0, "max_tokens": MAX_TOKENS},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    usage = body.get("usage") or {}
    choice = (body.get("choices") or [{}])[0]
    return {
        "text": (choice.get("message") or {}).get("content") or "",
        "finish": choice.get("finish_reason"),
        "in_tokens": usage.get("prompt_tokens"),
        "out_tokens": usage.get("completion_tokens"),
    }


def to_gemini_schema(node):
    """json_schema（大写 TYPE / anyOf）→ Gemini response_schema 形状。"""
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "type":
            out["type"] = str(v).upper()
        elif k == "properties":
            out[k] = {kk: to_gemini_schema(vv) for kk, vv in v.items()}
        elif k == "items":
            out[k] = to_gemini_schema(v)
        elif k == "anyOf":
            out[k] = [to_gemini_schema(x) for x in v]
        else:
            out[k] = v
    return out


def call_gemini(model: str, prompt: str, schema: dict, pngs: list[bytes],
                suffix: str) -> dict:
    """Gemini 走 response_schema 硬约束（schema 不进 prompt 正文）。"""
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    parts: list[dict] = [{"text": prompt}]
    for png in pngs:
        parts.append({"inline_data": {"mime_type": mime,
                                      "data": base64.b64encode(png).decode()}})
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": ENV["GEMINI_API_KEY"],
                 "Content-Type": "application/json"},
        json={"contents": [{"parts": parts}],
              "generationConfig": {"temperature": 0,
                                   "responseMimeType": "application/json",
                                   "responseSchema": to_gemini_schema(schema)}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    usage = body.get("usageMetadata") or {}
    cand = (body.get("candidates") or [{}])[0]
    try:
        text = cand["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        text = ""
    return {"text": text, "finish": cand.get("finishReason"),
            "in_tokens": usage.get("promptTokenCount"),
            "out_tokens": usage.get("candidatesTokenCount")}


def call_with_retry(model, prompt, schema, pngs, suffix) -> dict:
    fn = call_gemini if model.startswith("gemini") else call_qwen
    for attempt in range(3):
        try:
            return fn(model, prompt, schema, pngs, suffix)
        except (httpx.RemoteProtocolError, httpx.ReadTimeout,
                httpx.ConnectError, httpx.HTTPStatusError) as exc:
            if attempt == 2:
                raise
            # 429/5xx 退避重试；4xx（非429）不该重试
            if isinstance(exc, httpx.HTTPStatusError) and \
                    exc.response.status_code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


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


def remap_pages(entities: list, real_pages: list[int]) -> list:
    """模型输出的 page 是「所见图片序号」→ 映射回真实页号。"""
    for e in entities:
        if isinstance(e, dict) and isinstance(e.get("page"), list):
            e["page"] = [real_pages[p - 1] if 1 <= p <= len(real_pages) else p
                         for p in e["page"] if isinstance(p, (int, float))]
    return entities


def ocr_one(model: str, prompt: str, schema: dict, doc: pathlib.Path) -> dict:
    """单文件 OCR：整文件调用；截断/解析失败 → 页对半兜底。"""
    pngs, real_pages = render_doc(doc)
    suffix = doc.suffix.lower()
    r = call_with_retry(model, prompt, schema, pngs, suffix)
    parsed = parse_json(r["text"])
    meta = {"in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"],
            "finish": r["finish"], "chunked": False}
    if isinstance(parsed, list):
        return {"entities": remap_pages(parsed, real_pages), **meta}

    # 兜底: 疑似截断（finish=length 或解析失败）且多页 → 对半分
    if len(pngs) > 1:
        mid = len(pngs) // 2
        halves = [(pngs[:mid], real_pages[:mid]), (pngs[mid:], real_pages[mid:])]
        merged: list = []
        toks = [0, 0]
        for h_pngs, h_pages in halves:
            hr = call_with_retry(model, prompt, schema, h_pngs, suffix)
            hp = parse_json(hr["text"])
            toks[0] += hr["in_tokens"] or 0
            toks[1] += hr["out_tokens"] or 0
            if isinstance(hp, list):
                merged.extend(remap_pages(hp, h_pages))
        if merged:
            return {"entities": merged, "in_tokens": toks[0], "out_tokens": toks[1],
                    "finish": "chunked", "chunked": True}
    return {"entities": None, "raw_head": (r["text"] or "")[:400], **meta}


# ── run ──────────────────────────────────────────────────────────────────────

def run(variant: str, split_name: str, tag: str, model: str, workers: int,
        only: list[str] | None = None) -> None:
    prompt, schema = render(variant)
    prompt_sha = hashlib.sha256(
        (prompt + json.dumps(schema, sort_keys=True)).encode()).hexdigest()[:16]
    split = json.loads((JAPAN / "split.json").read_text())
    names = split[split_name] if split_name in split else sorted(
        n for s in split.values() for n in s)  # "all"
    if only:
        names = [n for n in names if n in set(only)]
    out_dir = OUT / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"变体={variant} split={split_name}({len(names)}) 模型={model} sha={prompt_sha}")

    def one(name: str) -> tuple[str, str]:
        cache = out_dir / (name + ".json")
        if cache.exists():
            try:
                c = json.loads(cache.read_text())
                if c.get("prompt_sha") == prompt_sha and c.get("model") == model \
                        and c.get("entities") is not None:
                    return name, "cache"
            except json.JSONDecodeError:
                pass
        doc = DOCS / name
        if not doc.exists():
            return name, "MISSING-DOC"
        try:
            res = ocr_one(model, prompt, schema, doc)
        except Exception as exc:  # noqa: BLE001 — 单样本失败不中断整批
            res = {"entities": None, "error": repr(exc)[:300]}
        res.update({"prompt_sha": prompt_sha, "model": model, "doc": name})
        cache.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        n_ent = len(res["entities"]) if isinstance(res.get("entities"), list) else -1
        return name, ("ok " + str(n_ent)) if n_ent >= 0 else "FAIL"

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (name, st) in enumerate(ex.map(one, names), 1):
            print(f"  [{i:3d}/{len(names)}] {st:8s} {name[:60]}", flush=True)
    fails = [p.name for p in out_dir.glob("*.json")
             if json.loads(p.read_text()).get("entities") is None]
    print(f"\n完成 {len(names)} 文件, 失败 {len(fails)}, 耗时 {time.time()-t0:.0f}s")
    if fails:
        print("失败:", fails[:10])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("variant")
    p.add_argument("--split", default="dev", help="dev/test/val/all")
    p.add_argument("--tag", required=True)
    p.add_argument("--model", default="qwen3-vl-plus")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--only", nargs="*", help="只跑指定文件名")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.variant, a.split, a.tag, a.model, a.workers, a.only)
    else:
        sys.exit(2)
