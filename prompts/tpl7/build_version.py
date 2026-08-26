"""从 v1 基线派生新版本：只做**加法补丁**，不重写原 prompt。

为什么是加法：v1 的正文里有大量已经生效的规则（票据切分 §1.0、跨页合并、
数值规范、字段清单），重写等于把它们全部置于风险之下。每轮只追加一个
「MY 报销贴单专项修正」段，放在字段清单之前 —— 位置靠后、指令更具体，
与前文冲突时以它为准（并在段首显式声明这一点）。

用法：
    python build_version.py v2 patches/v2.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
MARKER = "━━━ MY 报销贴单专项修正"


def build(version: str, patch_file: str) -> Path:
    base = json.loads((HERE / "v1_baseline.json").read_text())
    patch = Path(patch_file).read_text().rstrip()
    prompt = base["composed_prompt"]

    # 剥掉上一版的补丁段（若有），保证每版都从 v1 正文重新叠加，不会层层累积
    if MARKER in prompt:
        prompt = prompt.split(MARKER)[0].rstrip() + "\n"

    anchor = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n# 输出字段清单"
    block = f"\n{MARKER}（{version}）━━━\n{patch}\n\n"
    if anchor in prompt:
        prompt = prompt.replace(anchor, block + anchor, 1)
    else:                                   # 兜底：追加到末尾
        prompt = prompt.rstrip() + "\n" + block

    out = dict(base)
    out["composed_prompt"] = prompt
    out["patch_version"] = version
    dest = HERE / f"{version}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"{version}: prompt {len(base['composed_prompt'])} → {len(prompt)} 字符 "
          f"(+{len(prompt) - len(base['composed_prompt'])})")
    return dest


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
