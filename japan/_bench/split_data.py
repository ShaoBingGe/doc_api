"""日本数据集三分（dev/test/val）— 分层、确定性 seed=42。

分层键 = (实体数桶 single/multi/huge, 是否含 invoice, source)，组内 shuffle(42)
后按 4:3:3 轮转分配。落盘 japan/split.json。

**去泄漏**：语料里有 11 组「同一张原件被收录多次」（md5 相同、文件名不同）。若把同组
文件分到不同 split，dev 上的调优就会泄漏进 val，val 的 held-out 结论失去意义。
因此以 md5 组为不可分割单元分配，组内文件必落同一 split。
"""
from __future__ import annotations

import json
import pathlib
import random

JAPAN = pathlib.Path(__file__).resolve().parents[1]
RESULT = JAPAN / "result"
PATTERN = ["dev", "test", "val", "dev", "test", "val", "dev", "dev", "test", "val"]  # 4:3:3
SEED = 42


def build_split() -> dict:
    # md5 组 → 同组文件必落同一 split（去泄漏）
    from _bench.gt_conflicts import duplicate_groups  # noqa: E402
    dup_of: dict[str, tuple[str, ...]] = {}
    for grp in duplicate_groups():
        for n in grp:
            dup_of[n] = tuple(grp)

    files = []
    for f in sorted(RESULT.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        ents = d.get("entities") or []
        n = len(ents)
        inv = sum(1 for e in ents if e.get("docType") == "invoice")
        name = f.name[: -len(".json")]  # "<X>.pdf.json" → "<X>.pdf"
        files.append({
            "name": name,
            "unit": dup_of.get(name, (name,)),  # 分配单元：重复组或自身
            "n": n,
            "bucket": "huge" if n >= 10 else ("multi" if n > 1 else "single"),
            "has_inv": inv > 0,
            "src": d.get("source"),
        })

    # 以「单元」为粒度分层：单元属性取其首个文件的属性（组内文件同一张原件，属性一致）
    units: dict[tuple, dict] = {}
    for x in files:
        units.setdefault(x["unit"], x)

    groups: dict[tuple, list[dict]] = {}
    for u in units.values():
        groups.setdefault((u["bucket"], u["has_inv"], u["src"]), []).append(u)

    split: dict[str, list[str]] = {"dev": [], "test": [], "val": []}
    rng = random.Random(SEED)
    i = 0
    for key in sorted(groups):
        g = sorted(groups[key], key=lambda x: x["unit"])
        rng.shuffle(g)
        for u in g:
            split[PATTERN[i % len(PATTERN)]].extend(u["unit"])
            i += 1
    for s in split:
        split[s].sort()
    return split


if __name__ == "__main__":
    split = build_split()
    out = JAPAN / "split.json"
    out.write_text(json.dumps(split, ensure_ascii=False, indent=1))
    for s, names in split.items():
        print(f"{s:5s} {len(names)} files")
    print(f"写入 {out}")
