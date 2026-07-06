# 设计与开发计划 · 多行明细（line items / 数组字段）的增删改全链路

> 状态：待执行 ｜ 类型：**新功能**（非 bug 修复）
> 原则沿用前两轮：每阶段独立可交付、全量验证、独立提交、可随时停。

---

## 0. 问题与证据（2026-07 代码取证）

用户报告三个缺口，逐一核实：

| # | 论断 | 核实 | 根因位置 |
|---|---|---|---|
| 1 | 无法独立新增一个/一组 line 或 array 字段 | **成立** | `customize_fork._module_from_add_diff`（:704）：json_path 恒 `$[*].{name}`；`format=array` 只产 `{"type":"ARRAY"}` **无 items（无列定义）**。前端 `NewFieldRow`/`AddFieldList` 只收名称+格式，无列编辑 UI。overlay `added_fields` 条目无嵌套结构 |
| 2 | 只支持一组 array 字段输出 | **组装层不成立、实际成立** | composer 实测支持任意多个 `$[*].xxx[*]` 数组组（items schema 各自完整）；MY 模板本身有 3 个数组。真正限制：**多数组只能来自国家模板 yaml**（`template_loader._build_array_module`），客户加不了第二个——与 #1 同根 |
| 3 | JSON 上无法很好定义 array 输出 | **对客户新增的数组成立** | 模板数组 schema 良定义；客户新增的 `{"type":"ARRAY"}` 无 items → response_schema 对行结构零约束 → 模型自由发挥 |

**取证中额外发现的两个隐藏缺陷**（纳入本计划一并修）：

- **列改名半失效**：`customize_fork.py:288-292` 只对顶层标量名做 rename 传播
  （`"[" not in old_name`）。用户双击数组单元格改名（如
  `detailOfGoodsOrServices[0].quantity` → `qty`）产生的 diff 不改模块
  schema/json_path，只沦为一条文本反馈——schema 列名纹丝不动，用户以为改了。
- **数组单元格无跨样本反思语料**：`reflection_context.
  _build_cross_doc_context_for_diffs` 显式跳过含 `[`/`.` 的字段名——数组列的
  修正拿不到跨样本对照，反思只能凭单 cell 值猜。

**一句话根因**：整条「新增/结构编辑」链路（前端 draft → overlay → add-diff →
fork → module/schema → 反思）都按**顶层标量字段**设计；数组是黑盒——只能从
模板出生、只能改单元格值。

---

## 1. 目标与非目标

**目标**
1. 客户可以**新增带列定义的数组字段**（如自定义费用明细表），首轮即有正确的
   items schema 与列感知 prompt；
2. 对既有数组字段可以**加列 / 删列 / 改列名**，级联到 schema、prompt、标注行；
3. 数组列的修正获得**列级反思**（跨样本对照 + 按列聚合），不再糊进父模块文本；
4. （行级）客户可**补一行漏识别的 GT / 删一行多识别的 GT**，让 evaluator 的
   漏提取/多提取信号（批次 4 已有）真正驱动优化。

**非目标**
- 不改变「模块 = 整个数组」的粒度（列不建独立 OcrModule——模块爆炸 + 判官/
  评分/组装全要重做，收益不成比例）；
- 不做数组嵌套数组（`a[*].b[*]`，票据场景不存在）；
- 不做行序语义（行顺序由 OCR 决定；evaluator 批次 4 已按内容贪心对齐）。

---

## 2. 现状数据流地图（关键锚点）

```
前端 NewFieldRow/AddFieldList (rows.tsx:305 / panels.tsx)
  → store.addFieldDrafts {rowId, correctedName, correctedFormat, values{docId}}
  → flushDraftsToOverlay (store:1160)  POST commit-draft {new_name, field_type, added_value}
      → apply_draft Case2 (pending_edits_service:apply_draft)
      → domain/overlay.record_added_field  → overlay.added_fields[{field_name,type,description,…}]
  → submitCustomize (store:985)  diffs[{kind,module_key,original/corrected × name/value/format}]
      → customer_iteration._execute_pipeline → reflect_on_diffs (new_field skill)
      → customize_fork._fork_api_definition
          → _module_from_add_diff (:704)  json_path=$[*].{name}, fragment={type}   ← 缺口#1
          → _clone_module rename 传播 (:288  仅顶层名)                              ← 列改名半失效
      → composer.assemble_schema  （多数组组 OK，实测）
  → OCR → annotations field_name=`arr[N].col` → GT build 重组嵌套
  → ArrayTable (rows.tsx:363)  列=从已有输出反推，只读表+单元格双击编辑          ← 无行/列操作
  → evaluator._compare_lists  内容贪心对齐（批次4）→ 漏提取/多提取 diff           ← 信号已有，GT 缺入口
```

---

## 3. 设计决策（定稿）

### D1 · 新增数组 = 「带列定义的 added_field」，columns 全链路透传

一个新数组字段由 `{name, columns: [{name, type}], 每样本样例行?}` 定义。
`columns` 贯穿六层，每层改动都很小：

| 层 | 改动 |
|---|---|
| 前端 draft | `AddFieldRow` 增 `columns?: Array<{name: string; type: string}>`（store 类型） |
| flushDrafts body | `{new_name, field_type:'array', columns:[…]}` |
| `apply_draft` Case2 | 透传 `columns` |
| `domain/overlay.record_added_field` | 增可选 `columns` 参数，存进 added_fields 条目 |
| `submitCustomize` diffs | diff 增 `columns` 键（kind=add 且 format=array 时） |
| `_module_from_add_diff` | **array 分支**（见 D2） |

### D2 · `_module_from_add_diff` 的 array 分支（本计划的核心函数）

`format=array` 时不再走标量路径，改为镜像 `template_loader._build_array_module`
（:365，已验证的模板侧实现）：

```python
json_path = f"$[*].{new_name}[*]"          # 而非 $[*].{new_name}
schema_fragment = {                          # fragment 即 items schema（composer 尾随 [*] 注入 items）
    "type": "OBJECT",
    "properties": {c["name"]: {"type": _map(c["type"])} for c in columns},
}
# 无列时（裸值数组，如 tags）：fragment = {"type": "STRING"}——items 为字符串，
# 好过现状的「无 items 零约束」。
```

- 静态骨架 prompt 复用 `_build_array_module` 的样式（输出形式=JSON 数组、
  每行一个对象、列清单、找不到输出 `[]`）；
- `_llm_expand_new_field` 的 user_prompt 增加列清单与每列类型，system 指令
  要求产出**表定位锚点（表头文本）+ 行切割规则 + 每列取值要点**；
- composer 无需改动（`$[*].{name}[*]` 尾随 [*] 注入 items 已在批次 1 验证）。

### D3 · 列级结构编辑走 overlay 新键 `array_columns`（不污染顶层三映射）

```json
"array_columns": {
  "detailOfGoodsOrServices": {
    "added":   [{"name": "discount", "type": "number"}],
    "deleted": ["remark"],
    "renamed": {"quantity": "qty"}
  }
}
```

**为什么不复用顶层 renames/added/deleted**：顶层三映射的读取方遍布全链
（padding / projection / required set / rename 传播 / cascade），全部按「顶层
名」语义工作；塞 `arr[*].col` 点路径进去等于要求每个读取方数组感知，漏一处
就是静默 bug。独立键让读取方显式 opt-in。

**应用点**（fork 时，`_clone_module` 内对数组模块）：
1. `schema_fragment.properties`：加列 / 删列 / 改键名；
2. `ocr_prompt` 列清单行同步重写 + 加「新增列 X：客户样例值 …」小节；
3. **级联标注**：`domain/overlay` 增数组感知变体——
   `cascade_rename_array_column(db, api_def_id, arr, old, new)`（SQL：
   `field_name LIKE 'arr[%].old'` 逐行改写尾段）与
   `delete_array_column_annotations(...)`（同模式删除）。SQLite/PG 都用
   Python 端正则复核后逐条 update（行数 = 样本数×行数，量级安全）。

**列改名语义修正**（隐藏缺陷）：前端双击数组单元格、只改名不改值时，不再产
标量 rename diff，改走 `array_columns.renamed`（commit-draft 新 case）。
`customize_fork.py:288` 的顶层限制保留（它是对的），数组列走新通道。

### D4 · 列级反思：按列聚合 + 跨样本语料解禁

- `reflection_context._build_cross_doc_context_for_diffs`：对
  `arr[N].col` 形态的 diff 字段名，收集**该列在全部已审视样本的所有行值**
  （`field_name LIKE 'arr[%].col'`），渲染为「列跨样本对照」块（样本 × 行值
  列表）——替代现状的直接跳过；
- `customize_fork` 编辑聚合（:301 附近）：同一数组列的多条 cell diff 合并为
  一条**列级反馈**（`列 quantity：3 处修正 → [原值→正值]×3`），而非三条孤立
  的「客户在样本上修正 arr[2].quantity 的值为 …」文本行；
- 反思 diff 的 `original_name/corrected_name` 传列名（`arr[*].col` 规范形），
  `edit_intent.classify` 的值级判定（NORMALIZE/RETARGET/SUPPRESS，批次 5）
  自动对列生效，无需改；
- FieldRule（批次 5 落地）挂在父模块上，skeleton 渲染时列级规则以
  `- 列 quantity：…` 前缀区分。

### D5 · 行级 GT 编辑（加行/删行）= 纯标注层操作，不动 schema

- **加行**：ArrayTable 底部「+ 补一行」→ 按列输入 → 为每列建 manual
  annotation `arr[maxIdx+1].col`（走既有 `saveDocumentAnnotation`，
  is_corrected=True）。GT build（`ground_truth._insert` 自动 grow）即包含
  新行 → evaluator 对 OCR 输出产「漏提取」diff → optimizer 收到行完整性
  信号。**反思侧零改动，信号链路已在**（批次 4 的内容对齐 + 漏提取 diff）。
- **删行**：行悬浮「删除此行」→ 删该 idx 全部 annotations。**空洞问题**：
  GT build 对缺失 idx 产生空 dict `{}`，会造成对齐噪声——删行后必须**重排**
  后续行号（`arr[N].col` N>idx 的批量 -1，同 D3 的级联机制）。
- 不做「行内容当 schema」：行是数据不是结构，永不进 overlay。

### D6 · 兜底与显示（可选优化，不阻塞）

- `_pad_with_required_keys` 对 added_fields 中 type=array 的字段 pad `[]`
  而非 `null`（与模板 prompt「找不到输出空数组 []」一致）——需要 required
  set 携带类型，改动面偏大，标记 P1 可选项；
- ArrayTable 对「overlay 新增但尚未 OCR」的数组渲染空表（列头 + 「保存生成
  后识别」占位）。

---

## 4. 分阶段计划

依赖：P0 → P1 → P2 → P3（P2/P3 可换序；P3 的行级编辑不依赖 P2）。

> **进度**：P0 ✅（`353b78d`）、P1 ✅（`c5b8cae`）已完成；P2/P3 待做。
> 论断 #1/#3 已关死——客户可从 UI 创建带列明细表，response_schema 对行结构
> 有完整约束。P1 的「空表预览」（步 4）作次要 polish 延后（AddFieldList 的
> 列编辑器已提供保存前的可视反馈，保存+OCR 后 ArrayTable 出现）。

### P0 · 后端地基：带列新增数组（~1d）✅

| 步 | 文件 | 改动 |
|---|---|---|
| 1 | `domain/overlay.py` | `record_added_field` 增可选 `columns`；`_normalize` 保留该键 |
| 2 | `services/pending_edits_service.py` | facade 透传；`apply_draft` Case2 透传 |
| 3 | `ocr_optimizer/service/customize_fork.py` | `_module_from_add_diff` array 分支（D2）；`_llm_expand_new_field` 列感知 prompt |
| 4 | `customer_iteration._execute_pipeline` | add diff 从 overlay added_fields 取 columns（overlay 种子化路径 :222 的 synth diff 带 columns） |
| 5 | 测试 | `test_array_field_add.py`：带列新增 → 模块 json_path/fragment 正确 → `assemble_schema` round-trip（record.items.properties.{arr}.items.properties.{col} 齐全）→ 无列时 items=STRING；LLM 扩展 mock 校验列清单进 prompt |

**验证**：全量 pytest；黄金回归不受影响（不动模板路径）。
**DoD**：POST customize 带 `columns` 的 add diff → 新版本含正确数组模块，
response_schema 对行结构有完整约束（论断 #1、#3 关死）。

### P1 · 前端新增数组交互（~1d）✅

| 步 | 文件 | 改动 |
|---|---|---|
| 1 | `stores/workspace-store.ts` | `AddFieldRow`/`FieldEditDraft` 增 `columns`；`flushDraftsToOverlay`/`submitCustomize` 携带；**顺手收敛 :1025 `/customize` 裸 URL 进 api-client（B1 漏网）** |
| 2 | `field-viewer/rows.tsx` `NewFieldRow` | format 选 array 时展开内联列编辑器（列名+类型，可增删，≥1 列或留空=裸值数组） |
| 3 | `field-viewer/panels.tsx` `AddFieldList` | 行内展示列 chips；提交路径带 columns |
| 4 | `field-viewer/rows.tsx` `ArrayTable` | 渲染 overlay 新增数组的空表（列头 + 占位行） |

**验证**：`npm run build` + lint + preview 冒烟（新增数组 → 保存 → 空表出现
→ 触发定制 → OCR 后表格有数据）。
**DoD**：不写代码的客户可从 UI 完整创建一张带列的明细表。

### P2 · 列级结构编辑（~1.5-2d）

| 步 | 文件 | 改动 |
|---|---|---|
| 1 | `domain/overlay.py` | `array_columns` 键（_empty/_normalize）+ `record_array_column_add/delete/rename` + 两个级联函数（D3；含删行重排共用的批量改号工具） |
| 2 | `services/pending_edits_service.py` | facade（locked 注入：数组本身 country-locked 时拒绝列编辑）+ `apply_draft` 新 case `array_column: {array, op, name, new_name?, type?}` |
| 3 | `customize_fork._clone_module` | 数组模块应用 array_columns（fragment.properties 增删改 + prompt 列清单重写 + 改名追加映射指令） |
| 4 | 前端 `ArrayTable` | 列头悬浮菜单：改名/删列；表尾「+ 列」；调 commit-draft 新 case |
| 5 | 前端 `FieldEditPanel` | 数组单元格「改名」路径改走 array_column rename（D3 语义修正） |
| 6 | 测试 | 列增删改 → overlay → fork 后 schema/prompt/annotations 三方一致；级联改号幂等 |

**DoD**：列改名真正改到 schema 与标注（隐藏缺陷关死）；加列/删列全链生效。

### P3 · 列级反思 + 行级 GT（~1.5d）

| 步 | 文件 | 改动 |
|---|---|---|
| 1 | `ocr_optimizer/service/reflection_context.py` | 数组列跨样本收集（D4，替代 `[`/`.` 跳过） |
| 2 | `customize_fork` 编辑聚合 | cell diffs 按列合并为列级反馈；反思 diff 用 `arr[*].col` 规范名 |
| 3 | `field-viewer/rows.tsx` | 行悬浮删行 + 表底补行表单（D5） |
| 4 | `stores/workspace-store.ts` | `addArrayRow`/`deleteArrayRow` 动作（manual annotations + 重排调用） |
| 5 | `domain/overlay.py` 或 annotation 服务 | 删行重排（复用 P2 批量改号工具） |
| 6 | 测试 | 列级语料渲染快照；补行 → GT build 含新行 → evaluator 产漏提取 diff（信号闭环单测） |

**DoD**：数组列修正有跨样本证据与列级聚合；漏行可补、错行可删且不留空洞。

**总计 ~5 天**。P0 后端先行可独立合入（对现有 UI 零影响——没有前端入口时
`columns` 恒为空，行为与今天完全一致）。

---

## 5. 兼容性与风险

| 风险 | 缓解 |
|---|---|
| 存量 overlay 无 `columns`/`array_columns` 键 | `_normalize` 补默认空值（既有模式，overlay.py:60） |
| 存量「ARRAY 无 items」客户模块（此前加的裸数组） | P0 附带幂等回填：扫描 fragment=`{"type":"ARRAY"}` 且无 items 的模块 → items=STRING（沿用批次 1 回填模式）；不强改历史版本，只修 active/draft |
| `$[*].arr` 与 `$[*].arr[*]` 并存的 schema 冲突 | composer `_merge_schema` 已兼容（批次 1 注释），P0 测试加显式用例 |
| 列级级联误伤同前缀字段（`items` vs `itemsTotal`） | LIKE 初筛后 Python 正则 `^{arr}\[\d+\]\.{col}$` 精确复核再改写 |
| 列名与顶层字段重名 | 无冲突——annotation 命名空间带 `arr[N].` 前缀，schema 在 items 下 |
| country-locked 数组被改列 | facade 注入 locked 检查（P2 步 2），沿用 A1 机制 |
| 反向依赖回归 | 全部改动落在 domain / ocr_optimizer / 前端；`test_dependency_direction` 白名单零变化 |
| 前端无测试 | 沿用 B2 打法：tsc 严格 + build + preview 三路径冒烟 + 一步一 commit |

## 6. 完成定义（整轮）

- [ ] 客户可从 UI 新增带列数组字段，response_schema 对行结构有完整 items 约束
- [ ] 多个数组字段（模板 + 客户新增）可同时输出，JSON 预览正确
- [ ] 列加/删/改名全链生效（schema + prompt + 标注级联），列改名不再半失效
- [ ] 数组列修正获得跨样本对照与列级聚合反思
- [ ] 可补行/删行 GT，evaluator 漏提取/多提取信号闭环
- [ ] 全量 pytest 绿 + 新增数组链路单测 + 前端 build/lint/冒烟绿
- [ ] 黄金回归（模板数组路径）分数不降
