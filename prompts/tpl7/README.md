# template 7 prompt 版本管理

Chinkin（振兴）马来西亚报销票据模板的 prompt 迭代目录。
**线上不直接改**——每一版先在此落文件、评测通过后再推生产。

## 目录

```
v1_baseline.json      线上当前 prompt/schema 的快照（基线，只读）
patches/vN.md         第 N 版的补丁正文（人写的规则）
vN.json               v1 正文 + patches/vN.md 组装出的完整版本
vN.result.json        该版最后一轮的完整识别结果（供人工核对）
build_version.py      组装器：v1 + 补丁 → vN.json
evaluate.py           评测器：跑 N 轮、按 8 条判据打分
CHANGELOG.md          每轮改了什么、分数如何变化
```

## 为什么是「加法补丁」而不是重写

v1 正文里已有大量生效中的规则（票据切分 §1.0、跨页合并、数值规范、
字段清单）。整段重写等于把它们全部置于风险之下——改一个问题，
可能悄悄弄坏三个没人盯着的地方。

每版只追加一个「MY 报销贴单专项修正」段，插在字段清单之前：位置靠后、
指令更具体，并在段首显式声明「与前文冲突以本段为准」。
`build_version.py` 每次都从 v1 正文重新叠加，不会层层累积历史补丁。

## 评测判据（8 条）

判据来自人工核对过的票面事实，不是模型自评。样本为 25 页报销贴单
`July Claim 20260826`。

| 编号 | 检查 | 依据 |
|---|---|---|
| P1 | 第 2、3 页加油刷卡小票判 `receipt` | 票面虽印 INVOICE，但无买方、无税额拆分 |
| P2 | `nameOfInvoice` 输出票面抬头 | ≥60% 有效票据有值 |
| P3 | 加油小票单号取 `Reference No` | 不得是 8 位纯数字（Terminal）、不得重号 |
| P4 | 第 6 页 Agoda 取 MYR | 票面 `Total Charge MYR 224.97 (USD 54.98)` |
| P5 | `billFromCountry` / `CountryCode` | ≥60% 有效票据有值 |
| P6 | 明细行 `unitPrice` | ≥70% 明细行有值 |
| **K1** | 第 1 页保持 `other` | **已正确，不许改坏** |
| **K2** | 第 13–25 页保持 `other` | **已正确，不许改坏**（Touch'nGo 对账单） |

## 必须多轮评测

实测同一 prompt、同一模型连跑两次，`nameOfInvoice` 一次 0/25 全空、
一次 18/23 有值。**单轮结果分不清「prompt 改好了」与「模型这次手气好」**，
所以每版固定跑 3 轮，按「几轮通过」记分。

## 用法

```bash
# 1. 写补丁
vim patches/v3.md

# 2. 组装
python3 build_version.py v3 patches/v3.md

# 3. 上传并评测（服务器上有模型访问权）
scp evaluate.py v3.json root@<host>:/tmp/tpl7/
ssh root@<host> "cd /opt/docapi/backend && \
  ./.venv/bin/python -u /tmp/tpl7/evaluate.py /tmp/tpl7/v3.json --runs 3 --raw"
```

## 推上生产

评测达标后，把 `vN.json` 的 `composed_prompt` 写回
`OcrPromptVersion.composed_prompt`（templateId=7 的 active 版本）。
**注意**：走 `seed_open_api.refresh_prompt_from_country_template` 会用国家模板
覆盖回去——补丁若要长期保留，最终得合并进 `MY_invoice_prompt.yaml`。
