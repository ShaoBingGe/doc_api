# CLAUDE.md — 项目工作约定

> 当你接管这个项目时务必先读完本文。这里记录的是「客户编辑字段 → fork → 3 轮迭代」流程中**绝对不能违反**的工程约束，以及它们对应的代码位置。
>
> 完整 prompt 与流程参考 `docs/prompt-system.md`。

---

## 核心心智模型

```
原模板 (国家 YAML)
   │  customer 在工作区编辑 / 新增字段
   ▼
fork ──► 新 ApiDef（自带 api_code）+ v1 (manual_edit)
          │   • 复制源模块 + 应用客户编辑补丁
          │   • 新字段用 LLM 反思扩展 description / ocr_prompt
          │
          ▼
[3 轮迭代] 每轮 = 分拆 → 局部验证 → 重组
          │   1. OCR 所有"已审视"样本 → 评估每个 module 准确率
          │   2. 若 overall_accuracy ≥ 99.9% → 早停，复用当前版本
          │   3. 对失败 module 调 module_optimizer
          │   4. verify_module_fix 判官：accept 才采纳，reject 保留旧 prompt
          │   5. composer 组装新版本
          │
          ▼
最终激活版本（仍含**所有**初始 module）
```

---

## 五大原则（违反任一条 = bug）

### ① Prompt 三段平台契约（design v7）

**文件**:
- `backend/app/ocr_optimizer/models.py` — `OcrPromptVersion.country_global_text`
- `backend/app/ocr_optimizer/service/composer.py` — 渲染顺序 + 四段 GLOBAL_* 常量
- `backend/app/ocr_optimizer/assets/global_output_contract.yaml` — Part 3 平台资产
- `backend/app/ocr_optimizer/service/output_contract.py` — 资产 loader（lru_cache）

#### 四段平台契约（任何路径都不允许动）

| 常量 / 字段 | 内容 | 来源 |
|---|---|---|
| `GLOBAL_PREAMBLE` | 任务说明 + 输出格式约束 | 写死在 composer.py |
| `country_global_text` | Part 1 国家事实 + Part 2 字段识别要点 | yaml `# Part 1` → `# Part 3` 之间，存 DB 列 |
| `GLOBAL_OUTPUT_CONTRACT_DETAILS` | Part 3 输出装配契约 8 节 | `assets/global_output_contract.yaml`，loader 启动时 lru_cache |
| `GLOBAL_SELF_CHECK` | 输出前自检三条 | 写死在 composer.py |

#### 渲染顺序（design v7）

```
GLOBAL_PREAMBLE
country_global_text                  ← Part 1 + 瘦身 Part 2（无 Part 3 文本）
# 整体输出 Schema (schema reference)
GLOBAL_OUTPUT_CONTRACT_DETAILS       ← Part 3：3.1-3.8 平台装配规则
# 模块识别指令
## 1..N 字段模块
GLOBAL_SELF_CHECK
```

`composer.assemble_prompt(modules, *, country_global)` 是 **keyword-only 必传**。
fork / round 通过 `new_version.country_global_text = src_version.country_global_text` 继承 Part 1+2；Part 3 由 composer 在每次组装时从平台资产重新注入，**不快照、不可改写**。

#### Part 3 平台资产边界

- 客户、反思 agent、optimizer **不可改写** `global_output_contract.yaml`
- 仅平台工程师能改，且需 PR 审查 + 进程重启才生效
- country yaml 的 `# Part 3` 段是**人类阅读副本**（指向平台资产的 TOC），composer 运行时**不读** country yaml 的 Part 3 文本（template_loader 在 `# Part 3` 标记处截断 country_global_text）
- v1（preset_init 路径）特例：composed_prompt = raw yaml prompt_format + 附加 `GLOBAL_OUTPUT_CONTRACT_DETAILS`，确保 Part 3 从第一次 OCR 起就生效

| ❌ 错误 | ✅ 正确 |
|---|---|
| 把国家全局规则塞进 OcrModule 表 | 用 `OcrPromptVersion.country_global_text` 列 |
| 把输出契约（去千分位 / qty×price 校验）写进 country yaml | 改 `global_output_contract.yaml`，PR 审查 |
| round / fork 改写 country_global_text | 直接从 src_version 拷贝到 new_version |
| 反思 agent 修改 Part 3 装配规则 | 反思只能改 Part 2（字段 schema description / ocr_prompt） |
| 在 OcrModule 表里搜 `module_key='global_rules'` | 已迁移；只查 `country_global_text` 列 |
| 在客户/反思路径动 GLOBAL_PREAMBLE / GLOBAL_OUTPUT_CONTRACT_DETAILS / GLOBAL_SELF_CHECK | 仅可在 composer.py / 平台 yaml 源码层修改 |

---

### ② 字段 meta 元素必须保留

**关键文件**:
- `backend/app/ocr_optimizer/models.py` — `OcrModule`
- `backend/app/ocr_optimizer/service/persistence.py:clone_modules_to_new_version`
- `backend/app/ocr_optimizer/service/run_orchestrator.py:_run_one_round`
- `backend/app/ocr_optimizer/service/customer_iteration.py:_execute_pipeline` （**fork 时取版本的逻辑**）

#### 约定

1. **客户迭代路径必须传 `enable_meta=False`**
   - 调用 `start_optimization` / `advance_round` 时必传。
   - `_run_one_round` 看到 `enable_meta=False` 跳过 meta_optimizer，**不允许** add / remove / rename。
   - 即便未来某条路径开启 meta，`_run_one_round` 内部仍有两道守护：
     - `aggregate_accuracy ≥ 0.5` 的 module 不能被删
     - 删除后投影 module 数 < `max(MIN_SAMPLES, 半数)` → 该轮所有 remove 撤销

2. **fork 时取「模块最多」的源版本，不是 active**
   - 在 `_execute_pipeline` 里 SQL 聚合：每个 OcrPromptVersion 关联的 OcrModule 数量，取最多者；同模块数取最新。
   - 当 active 版本因历史 bug 模块被毁时，自动 fall back 到更早的好版本。
   - 不一致时打 `logger.warning` 让运维可见。

3. **per-module optimizer 只能改 prompt / description / suggestions**
   - 在 `module_optimizer.SYSTEM_INSTRUCTION` 里明确禁止 `skill_ids` / `new_skills`。
   - Pydantic `extra='forbid'` 校验拒绝其他字段。

4. **永远不要 SQL 删除 OcrModule 行**
   - 历史 module 是审计资产。状态切换用 `status='archived'`，不用 DELETE。

---

### ③ Composer 组装必须可回退

**文件**: `backend/app/ocr_optimizer/service/composer.py`

#### 约定

1. `assemble_schema` 遇 json_path 冲突 → **raise ValueError**，不要静默覆盖。
2. 调用方（`_run_one_round` step 5、`_fork_api_definition`）必须 `try / except ValueError`：
   - round 末尾：`next_version_id = current_version.id`（不退回会破坏链）
   - fork 时：raise `ValidationError`，前端能看到失败原因
3. 模块顺序由 `order_index` 保证，**不允许**在 composer 内重排。
4. composer **不调用 LLM**。是纯字符串拼接 + JSON merge。任何"用 LLM 整理 composed_prompt"的改动必须新建独立函数，不要污染 composer。

---

### ④ 下一轮迭代必须先做"门口认证"

**文件**: `backend/app/ocr_optimizer/service/run_orchestrator.py:_run_one_round`

```
每轮入口：
  1. OCR 用 current_version.composed_prompt 跑所有 confirmed 样本
  2. 逐 module 评估 accuracy
  3. ── 早停门口 ──
     if overall_accuracy ≥ 0.999:
         next_version_id = current_version.id   ← 复用，不创建新版本
         phase = completed
         return                                  ← 不做 step 3-5
  4. 仅对 accuracy<1.0 的 module 跑 optimizer
  5. verify_module_fix 判官：reject 就丢弃，保留旧 prompt
  6. compose 出 v(N)
```

#### 约定

- **早停必须在 step 3 之前**（OCR+eval 之后立刻判断），不能放到 round 末尾。一旦进 step 3，optimizer 可能挥棒乱打。
- `next_version_id = current_version.id` 是合法的"无变化"信号，**不算 round failed**。
- verify_module_fix **fail-open**：LLM 异常默认 accept，不阻塞进度；但要把异常记到 `optimization_suggestion` 字段里留痕。

---

### ⑤ 反思必须按 skill 分流且可累积

**文件**: `backend/app/ocr_optimizer/reflection/`

#### 约定

1. **反思 skill 是 YAML 资产**，路径 `reflection/skills/*.yaml`，由产品技术维护（非客户面向）。
2. 一个 diff 可以匹配**多个** skill，每个 skill 各产出一段 fix_suggestion。
3. 在 `_fork_api_definition` 里，同一个 `module_key` 收到多条 diff 时**累积**所有 fix_suggestion / corrected_value 到 prompt 后缀，**不要后写覆盖**。
4. 新字段（`kind='add'`）的 LLM 扩展必须在 **fork 前** 完成，给 round 1 一个完整可用的起点；不要指望优化器去填空白模板。
5. LLM 调用走 `llm_text_completion_failover`，配置链 `LLM_FALLBACK_CHAIN=gemini|gemini-2.5-flash;gemini|gemini-2.5-pro;mock|`。失败时静默降级到 `mock`，不阻塞 job。

#### Skill 编写守则

新增 skill 时一份 yaml 文件包含：
```yaml
key: <唯一>
display_name: <UI 显示名>
version: <整数，prompt 改时 bump>
match:
  diff_kind: edit | add
  <附加谓词>: <bool>
prompt: |
  <system 已统一，这里只写 user prompt 模板>
  必须最终要求 LLM 返回严格 JSON，键约定见 reflector.py:ReflectionResult
```

---

## 数据模型不变量

| 模型 | 约束 |
|---|---|
| `OcrPromptVersion.composed_prompt` | UTF-8 字符串，包含 GLOBAL_PREAMBLE 前缀。不允许为空。 |
| `OcrPromptVersion.composed_schema` | dict，含 `type:"object"`、`properties` 至少含 1 个 key |
| `OcrPromptVersion.country_global_text` | 国家全局规则文本（design v6）。fork / round 一律继承，不允许 round 内修改。 |
| `OcrModule.json_path` | `"$"` / `""` 表示全局（贡献到 root.properties）；其他用 jsonpath-lite 语法 |
| `OcrModule.skill_ids` | 只读，HARD COPY，optimizer 不能写 |
| `Annotation.is_corrected` | True = GT。由客户在工作区显式"已审视"或编辑触发；**不允许自动批量 True** |

---

## 客户 customize 流程（命令式总结）

1. ✅ **接 diffs** → `submit_customize_job` 入库
2. ✅ **反思** → `reflect_on_diffs(diffs, modules_by_key, …)`，每个 diff 产出 ReflectionResult
3. ✅ **fork**：
   - 选源版本：**模块最多** 的那个版本，**不是 active**
   - clone 所有源模块到 new_version，累积应用补丁
   - 新字段用 LLM 扩展（new_field skill + `_llm_expand_new_field`）
   - composer.assemble → raise on conflict
4. ✅ **样本门口**：confirmed_count < MIN_SAMPLES_FOR_ITERATION → `waiting_for_samples`；客户上传 + "已审视"超过门槛后 `maybe_auto_resume_for_api` 触发
5. ✅ **3 轮**：`enable_meta=False` 全程；每轮 round-start 早停；module-level verifier accept/reject
6. ✅ **finalize**：activate 表现最好的版本

每一步的失败必须**降级**而不是抛 500：
- OCR 失败 → doc.status=failed，job 继续
- LLM 失败 → failover chain → mock；job 继续
- compose 失败 → reuse 上一版本；round 标 failed 但 job 不挂

---

## 调试 / 审计入口

| 任务 | 方法 |
|---|---|
| 看某 ApiDef 的所有版本和 module 数 | `docs/prompt-system.md` 末尾的 audit 脚本 |
| 看某 job 的 reflection_summary | `GET /api-definitions/customize-jobs/{job_id}` |
| 看 round 的失败原因 | `OcrModuleIteration.optimization_suggestion` 含 verifier reject 注释 |
| 看 meta 决策 | `OcrOptimizationRound.meta_decision`（客户路径下 = 占位空对象） |
| Reflection 没生效 | 检查 `skill_count > 0`；为 0 通常是 LLM 不通或 match 谓词没命中 |

---

## 当前已知的"反模式"（不要复刻）

- ❌ 用 `persistence.get_active_version` 当作"源真相"：active 可能被历史 bug 损坏。改用模块最多的版本。
- ❌ 一次性删一组 module：哪怕是 meta optimizer 的建议，也必须过 well_performing 守护 + 半数门槛。
- ❌ 让客户上传后 OCR 出错就 500：上传是原子的，OCR 失败仅标 `doc.status=failed`。
- ❌ Auto-mark OCR 输出为 GT：必须客户显式"已审视"。
- ❌ 客户 customize 路径里启用 meta_optimizer：customer 已经手工拍板了模块集，meta 没有授权动它。

---

## 当我做改动时

1. 改 `_run_one_round` 时永远问：这条路径会不会被客户迭代触发？→ 是 → 必须传 `enable_meta=False` 一路。
2. 改 `_fork_api_definition` 时永远问：源版本选错了会怎样？→ 跑一遍 SQL 聚合的源版本选择。
3. 改 composer 时永远问：这次改的字符串会不会让某个老 prompt 解析不出 schema？→ 跑 round-trip 测试。
4. 改 reflection skill 时永远问：这条新 skill 是否和已有的重叠？→ master 路由会**全部触发**重叠的，下游累积。
