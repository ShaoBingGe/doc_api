# 实验 · v6 vs v6.1 —— response_schema 能否替代 prompt 正文的「输出字段清单」

> 日期：2026-08-28 ｜ 分支 `feat/schema-passed-to-model`
> 脚本：`backend/eval_samples/schema_ab/v61_experiment.py --rounds 3`
> 模型：gemini-3.5-flash · temperature=0 · seed=42 · thinking=low（与线上同参）

## 1. 设计

`prompts/tpl7/v6.json` 的正文里有一段 **「输出字段清单（键名必须逐字一致）」**，
约 2,156 字符（占正文 17.1%），内容是 schema 的文字复述：键名 + 类型 + enum。
既然 `response_schema` 现在真的会下发，这段理论上可由硬约束替代。

| arm | prompt 正文 | 是否传 schema | 含义 |
|---|---|---|---|
| `v6-noschema` | 原样 12,632 字符 | ❌ | **当前生产行为**的参照系 |
| `v6` | 原样 12,632 字符 | ✅ | 清单与 schema 双写 |
| `v6.1` | 删清单 10,476 字符 | ✅ | 只靠 schema 硬约束 |

三者**规则完全相同**，唯一差异是那一段 → 差异可归因。
3 样本（S1 单页扫描 / S2 六页六张 / S3 Credit Note）× 3 轮 = 27 次真实调用。

## 2. 结果

| arm | 平均耗时 | 输入 token | 输出 token | 准确 |
|---|---|---|---|---|
| v6-noschema | **6.6s** | 7,664 | 1,479 | 51/54 |
| v6 | 7.4s | 7,664 | 1,576 | **51/54** |
| v6.1 | 7.9s | **7,042** | 1,555 | 48/54 |

v6.1 相对 v6：输入 token **−622（−8.1%）**、耗时 **+0.5s**、准确 **51→48**。

3 轮几乎零抖动（同一 arm 同一样本的输出稳定一致），因此差异是系统性的。

### 2.1 逐样本

| 样本 | v6-noschema | v6 | v6.1 |
|---|---|---|---|
| S1 单页扫描 | 7/7 ×3 ✅ | 7/7 ×3 ✅ | 7/7 ×3 ✅ |
| S2 六页六张 | ❌ 6 张全判 Tax Invoice | ✅ 全对 | ❌ 3 张判 Tax Invoice |
| S3 Credit Note | ✅ −709.25 | ❌ 709.25（负号丢失）| ❌ 709.25 |

**v6.1 的错误 = v6 的错误 ∪ v6-noschema 的错误**。

## 3. 结论

### 3.1 字段清单段**不能删**（v6.1 方案否决）

省 8.1% 输入 token 换来 S2 分类能力下降。原因：这段不只是键名复述，
它把 **enum 候选值内联在了正文里**（`docType: string（enum: invoice | receipt）`、
`invoiceType: string（enum: Commercial Invoice | … | Tax Invoice）`）。
`response_schema` 的 enum 只保证**输出值合法**，不提供**如何在候选间选择**的语义引导；
正文里的清单则参与了模型的推理过程。两者不是替代关系。

顺带：省下的 622 token **没换来速度**（v6.1 反而 +0.5s）。耗时主要由输出 token
与网络决定，输入少几百 token 影响可忽略——「删正文省钱」这条路的收益本就有限。

### 3.2 S3 负号丢失是 **v6 的规则缺口**，不是 schema 的问题

v6 传 schema 时 Credit Note 的 `totalAmount` 稳定丢负号（709.25，3/3）。
但**当前模板 7_13 传 schema 时负号 3/3 稳定保住**（−709.25，单独复验）：

```
7_13 不传schema r0/r1/r2: totalAmount=-709.25
7_13 传  schema r0/r1/r2: totalAmount=-709.25
```

即：schema 硬约束本身不破坏负号；v6 是规则不够严密，schema 把缺口暴露了。
这与 Phase 2 的两次发现同构（Tax Invoice 判据含糊、BRN 自检漏括号旧号）——
**schema 一通，长期没被读过的 description / 规则债当场显形**。

### 3.3 对「开关是否默认打开」的意见

**技术上 MY 模板已是净收益，但建议默认值仍保持 `False`**，理由是部署纪律不是技术疑虑：

支持打开的证据（均在 MY 7_13 上）：
- Phase 2：3 样本 × 3 轮 = 18/18，开=关；
- Phase 3：黄金集 4 个 seed，开 ≥ 关（一次 +15.6pp，其余持平）；
- 本实验：S3 负号开/关都正确；
- 三次观察到的「开启后变差」根因**全部**是模板规则缺口，修法都是改规则而非关开关。

尚缺的两环：
1. **只在 MY 上验证过。** JP 模板的 description 从未被模型读过，规则质量未知；
   按 MY 的经验（一通就暴露 2–3 处规则债），JP 很可能同样需要先修一轮。
   而仓库根缺 `JP_invoice_prompt.yaml`，本地无法验证。
2. **没有真实流量灰度**（Phase 4 按指示暂缓）。

默认值改 `True` 意味着**任何未来部署静默进入新行为**，包括尚未验证的国家模板。
建议路径：先按部署 runbook 在腾讯云用 `.env` 打开做灰度 → 稳定后再改默认值 →
那时才谈得上把 MY §1.34 搬回 description（见 plan Phase 5）。
