# 新国家上线 Checklist（New-Country Onboarding）

> 把「加一个国家」从手工摸索沉淀为**可复制流水线**。本文档由 JP（日本）首次完整落地逼出，
> 全程复用平台既有的、**国别无关**的机器（template_loader / preset_init / build_golden_set /
> harness / golden_loop），新国家只写**国别增量**：模板 + 黄金集 + 领域 agent。
>
> 核心不变量见 [CLAUDE.md](../CLAUDE.md) §二（国别层 vs 全球层）。下面每步都标注产物落在哪一层。

---

## 0. 前置：拿到带人工标注的样本集

需要 `<国家>/` 一份 PDF + 人工标注 JSON 配对集（如 `Japan-inv/train/labels/*.pdf.json`）。
标注是**黄金集 GT 的唯一来源**，必须人工核验、字段名稳定。建议 ≥30 对带标注，最终入选 ≥15。

> JP 实例：`Japan-inv/` 含 train 182 / val 73 / test 108 对，每个 label 形如
> `{entities:[{docType, invoiceNumber, totalAmount, billFromName, ...}]}`。

---

## 1. 定稿字段集（国别层 · 数据驱动）

**红线：黄金集 GT 的键必须与模板 `json_schema` 字段名逐字对齐**，否则 harness 切片全部落空、判 0 分
（见 `ground_truth.align_for_path` / `slicer.extract`）。所以字段集与命名**以标注侧为准**，不要照搬别国模板的命名。

1. 统计标注的字段覆盖率，选 8–10 个高覆盖**标量**核心字段（行项目数组复杂，首版可暂弃）。
   ```bash
   cd backend
   # 一次性覆盖率统计脚本见 git 历史；或手写 Counter 跑一遍 labels/
   ```
   > JP 数据驱动结果（182 标注，全部 ≥80%）：docType, nameOfInvoice, invoiceNumber,
   > invoiceDate, currency, totalAmount, totalTaxAmount, billFromName,
   > billFromTaxIdentificationNumber, billToName（+ 结构字段 page）。

2. 写 `<COUNTRY>_invoice_prompt.yaml`（仓库根，只读资产）。结构：
   - `prompt_template.prompt_format`：**Part 1**（国家事实：票据分类/语言/货币/日期/税号规则）
     +**Part 2**（defer 给 schema description）+**Part 3**（仅引用，运行时 composer 从
     `assets/global_output_contract.yaml` 注入——**不要复制规则进来**）。
   - `prompt_template.json_schema`：`type: ARRAY` → `items.anyOf[0]` = invoice 分支，
     `properties` 列出定稿字段，每个字段写本地化 `description`（关键词/锚点/格式/易混辨别）。
   - Part 1 的标记 `# Part 1` 与 `# Part 3` 是 template_loader 截取 `country_global_text` 的边界，**勿改**。

3. 校验模板能被正确分解：
   ```bash
   cd backend && PYTHONPATH=. python -c \
     "from app.ocr_optimizer.service import template_loader as t; \
      d=t.decompose_country_template('JP'); \
      print(len(d['modules']),'modules'); \
      [print(m['order_index'], m['json_path']) for m in d['modules']]"
   ```
   预期：每个字段一个 module，`json_path` 形如 `$[*].<fieldName>`，叶名 == 标注键。

> 完整字段版可留 `<COUNTRY>_invoice_prompt.full.bak` 备查随时恢复。

---

## 2. 建黄金集（国别层 · 复用全球工具）

黄金评测**强耦合 DB 文档**（harness 通过 `doc.storage_path` 跑真实 OCR，manifest 的
`source_doc_id` 是 DB UUID）。所以先把标注样本「像真实客户那样」入库，再用既有 builder 冻结。

1. **入库**（模拟客户回路：建 ApiDef + Document + 人工 GT Annotation）。
   JP 用一次性脚本 `backend/app/ocr_optimizer/eval/seed_jp_golden.py`，新国家照抄改字段映射即可：
   ```bash
   cd backend
   PYTHONPATH=. python -m app.ocr_optimizer.eval.seed_jp_golden --limit 20
   ```
   产物：`jp-invoice-<hex>` ApiDef + 20 个 Document（GT 为 `source=manual, is_corrected=True`）。
   > 入选规则：单 entity（黄金 GT 根=单票）、docType∈{invoice,receipt}、核心字段 ≥8 个非空。

2. **冻结黄金集**（既有、国别无关 builder）：
   ```bash
   PYTHONPATH=. python -m app.ocr_optimizer.eval.build_golden_set --country JP --min-fields 8
   ```
   ⚠️ `--min-fields` 默认 12，但 10 字段集**最多 11 个叶子**，必须降到 **8**，否则全被当「thin」跳过。
   产物：`backend/app/ocr_optimizer/eval/golden_set/JP/{docs,ground_truth,manifest.json}`，
   GT 已 `[gt]` 包裹 + 去空（每个留存字段都可验证）。

---

## 3. 写国别领域 agent（国别层）

`backend/app/ocr_optimizer/reflection/country_agents/<COUNTRY>/{edit_field,add_field}.yaml`。
配了 country agent 后，反思走**国别 agent 而非全球 skills**（`reflector.py` 的 `if agent: ... else: route(diff)`），
edit_intent 分类仍作为证据注入。照抄 MY/JP 模板，把 `system_prompt` 换成本国易错点。

> JP 注入：登録番号 T+13（仅开票方，勿当请求书番号）、和暦转西暦、10%/8% 双税率、
> 御中/様 方向判定、円/¥ 整数去千分位。

校验加载：
```bash
PYTHONPATH=. .venv/bin/python -c \
  "import app.models; from app.ocr_optimizer.reflection.country_agents_loader import list_countries; \
   print(list_countries())"
```

---

## 4. 验证流水线打通

1. **键对齐自检（无需 OCR 后端，必跑）**：把 GT 当作 OCR 输出回灌做严格评分，应 == 1.0。
   证明 GT 键与 json_path 完全对齐、无「根类型不匹配假 0 分」bug。
   ```bash
   PYTHONPATH=. python -c \
   "from app.core.database import SessionLocal; \
    from app.models.api_definition import ApiDefinition; \
    from app.ocr_optimizer.models import OcrModule, OcrPromptVersion, PromptVersionStatus; \
    from app.ocr_optimizer.eval.harness import module_specs_from_orm, score_outputs; \
    from app.ocr_optimizer.eval.golden_loop import load_golden; \
    db=SessionLocal(); api=db.query(ApiDefinition).filter(ApiDefinition.api_code.like('jp-invoice%')).first(); \
    v=db.query(OcrPromptVersion).filter(OcrPromptVersion.api_definition_id==api.id, OcrPromptVersion.status==PromptVersionStatus.active.value).first(); \
    specs=module_specs_from_orm(db.query(OcrModule).filter(OcrModule.prompt_version_id==v.id).all()); \
    g=load_golden('JP'); gts={d:e['gt'] for d,e in g.items()}; \
    print('overall', round(score_outputs(specs,gts,gts,strict=True).overall_accuracy,4))"
   ```
   预期 `overall 1.0`（每个 module 20/20）。

2. **真实基线（需可用 OCR 后端，量化上线成本）**：
   ```bash
   # --processor 省略 → 跟随 .env 的 DEFAULT_PROCESSOR
   PYTHONPATH=. python -m app.ocr_optimizer.eval.run_golden_batch \
     --country JP --candidate jp-invoice-<hex> --size 5 --seed 42
   ```
   输出严格 overall + 每字段命中 + 偏差（每条偏差可喂 `reflect_on_golden` 驱动模板自迭代）。
   > 这是平台侧黄金门槛（改全球机器后回归用），**永不进客户路径**（CLAUDE.md §2.3）。
   >
   > ⚠️ **OCR 后端按部署选**：大陆服务器用阿里云 **千问/DashScope**（`DEFAULT_PROCESSOR=qwen` +
   > `QWEN_API_KEY`，因大陆访问不了 Gemini）；海外用 gemini。本机若只配了 gemini 且网络不通，
   > 基线会全 0（OCR 失败），不代表真实准确率——换成部署实际的 processor 再跑。

---

## 5. 产出清单与成本

一个新国家完成后应有：

| 产物 | 路径 | 层 |
|---|---|---|
| 国家模板 | `<COUNTRY>_invoice_prompt.yaml`（+ `.full.bak`） | 国别 |
| 黄金集 | `backend/.../eval/golden_set/<COUNTRY>/{docs,ground_truth,manifest.json}` | 国别 |
| 领域 agent | `backend/.../reflection/country_agents/<COUNTRY>/{edit,add}_field.yaml` | 国别 |
| 入库脚本 | `backend/.../eval/seed_<country>_golden.py`（照 JP 改字段映射） | 工具 |

**机器零改动**：composer / harness / golden_loop / reflector / template_loader / preset_init /
build_golden_set 全程未动——这即「全球机器 + 国别皮肤」「机器国别无关」的实证。

**JP 量化成本**：模板定稿 + 黄金集（20 样本）+ 双 agent + checklist ≈ 一个工作日量级；
最贵环节是「字段集定稿（需懂票面）」与「拿到人工标注」，二者均为国别一次性投入。
