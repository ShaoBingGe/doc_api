# 海信日本发票正确数据集

115 个已标注发票/收据文件，原件与结构化标注数据一一对应，供发票识别模型训练/评估使用。

## 数据规模

- **文件数：115 个**（docs/result 一一对应）
- **独立发票/收据数据条数：397 条**（即所有文件 `entities` 数组条目总和）
  - 其中 invoice（发票）：35 条
  - receipt（收据）：283 条
  - other（非发票/收据内容，如混贴单里的空白页、分隔页等）：79 条
  - 剔除 other 后，真正有财务信息的发票+收据数据为 **318 条**

## 目录结构

```
docs/       115 个发票原件（pdf/jpg/jpeg/png）
result/   115 个 <原件文件名>.json（结构化标注数据）
```

`docs/X.pdf` 对应 `result/X.pdf.json`。

## 标注数据说明

每个 json 的 `entities` 数组中，一个元素代表该文件里识别出的一份发票/收据/其他内容，常见字段包括：`docType`、`invoiceNumber`、`invoiceDate`、`totalAmount`、`currency`、`billFromName`、`billToName`、`detailOfGoodsOrServices` 等（按实际内容填充，不是每条都齐全）。

其中 **54 个文件**（json 里 `source: manual`）额外带 `_evidence` 字段，标出每个字段取值在原文中的出处（哪一页、原句是什么），可用于校验抽取准确率；其余 **61 个文件**（`source: excel_migrated`）没有这个字段。

### 注意：一个文件可能对应多条数据（混贴场景）

不少原件是把多张发票/收据贴在同一份文件里扫描的，这种情况下 **一个文件仍然只对应一个 json，但该 json 的 `entities` 数组里会有多条记录**，每条都是一份独立发票/收据的完整数据，互不影响。例如 `docs/000005.pdf` 是一张贴了 61 张小票的扫描单，`reviewed/000005.pdf.json` 只有一个文件，但 `entities` 数组里有 61 条记录。

数据集中 115 个文件里有 **51 个（约 44%）属于这种一对多情况**。使用时请遍历每个 json 的 `entities` 数组逐条取用，不要假设一个文件只有一条标注数据。

## 已知限制

- `excel_migrated` 的记录中，若原始 `page` 信息缺失，统一按第 1 页处理，对应 entity 会带一条 `_migration_note` 说明。
