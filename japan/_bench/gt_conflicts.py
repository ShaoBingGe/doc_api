"""GT 冲突检测 —— 客观、可复现地识别不可信的标注。

依据：语料里有 11 组「同一张原件被收录多次」（md5 相同、文件名不同），其中 10 组
被人工标注了**不同的结果**。同一张图两次标注不一致，说明该字段的标注口径本身不确定，
任何模型都不可能同时答对两边。这类字段从计分中剔除是有硬证据的，不掺主观判断。

产出 `japan/gt_exclusions.json`：
  {
    "field_level": {"<文件名>": {"<实体索引>": ["字段名", ...]}},   # 组内取值冲突的字段
    "entity_count_conflict": [["文件A","文件B"], ...],              # 组内实体数都不一致的组
    "groups": [["文件A","文件B"], ...]                              # 全部重复原件组（供 split 用）
  }

用法: python japan/_bench/gt_conflicts.py
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib

JAPAN = pathlib.Path(__file__).resolve().parents[1]
DOCS = JAPAN / "docs"
RESULT = JAPAN / "result"

SKIP_KEYS = {"序号"}


def duplicate_groups() -> list[list[str]]:
    """按内容 md5 归组，返回含 ≥2 个文件的组（组内文件名排序、组间稳定）。"""
    by_md5: dict[str, list[str]] = collections.defaultdict(list)
    for p in sorted(DOCS.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            by_md5[hashlib.md5(p.read_bytes()).hexdigest()].append(p.name)
    return sorted((sorted(v) for v in by_md5.values() if len(v) > 1))


def entities(name: str) -> list[dict]:
    return json.loads((RESULT / f"{name}.json").read_text(encoding="utf-8")).get(
        "entities") or []


def build() -> dict:
    groups = duplicate_groups()
    field_level: dict[str, dict[str, list[str]]] = {}
    count_conflict: list[list[str]] = []

    for grp in groups:
        es = {n: entities(n) for n in grp}
        if len({len(v) for v in es.values()}) > 1:
            count_conflict.append(grp)
            continue
        n_ent = len(next(iter(es.values())))
        for idx in range(n_ent):
            keys: set[str] = set()
            for n in grp:
                keys |= {k for k in es[n][idx] if not k.startswith("_")
                         and k not in SKIP_KEYS}
            for k in keys:
                vals = [es[n][idx].get(k) for n in grp]
                if len({json.dumps(v, ensure_ascii=False, sort_keys=True)
                        for v in vals}) > 1:
                    for n in grp:
                        field_level.setdefault(n, {}).setdefault(str(idx), [])
                        if k not in field_level[n][str(idx)]:
                            field_level[n][str(idx)].append(k)
    for n in field_level:
        for idx in field_level[n]:
            field_level[n][idx].sort()
    return {"field_level": field_level, "entity_count_conflict": count_conflict,
            "groups": groups}


if __name__ == "__main__":
    out = build()
    path = JAPAN / "gt_exclusions.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    n_fields = sum(len(v) for f in out["field_level"].values() for v in f.values())
    print(f"重复原件组 {len(out['groups'])} 组")
    print(f"实体数冲突组 {len(out['entity_count_conflict'])} 组: "
          f"{out['entity_count_conflict']}")
    print(f"字段级冲突 {n_fields} 项，涉及 {len(out['field_level'])} 个文件")
    print(f"写入 {path}")
