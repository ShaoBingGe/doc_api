# CLAUDE.md — 项目工作约定

> 当你接管这个项目时务必先读完本文。这里记录的是「客户编辑字段 → fork → 3 轮迭代」流程中**绝对不能违反**的工程约束，以及它们对应的代码位置。
>
> 完整 prompt 与流程参考 `docs/prompt-system.md`。

---

## 核心心智模型（Phase 19 起：单工作区）

```
原模板 (国家 YAML)
   │  customer 在 source ApiDef 的工作区编辑 / 新增 / 删除字段
   │  + 上传至少 3 个样本并逐一标记"已审视"
   ▼
"customize" ──► 在 SAME source ApiDef 上创建新 OcrPromptVersion (origin=manual_edit)
                │   • 复制源模块 + 应用客户编辑补丁（rename / add / delete）
                │   • 新字段用 LLM 反思扩展 description / ocr_prompt
                │   • 旧版本置 archived，source.prompt_version_id 指向新版本
                │   • source.api_code 不变（caller 集成无影响）
                │   • 不创建新 ApiDef、不切换 URL、不 rebind 文档
                │
                ▼
[3 轮迭代] 每轮 = 分拆 → 局部验证 → 重组（跑在 source.id 上）
                │   1. OCR 所有"已审视"样本 → 评估每个 module 准确率
                │   2. 若 overall_accuracy ≥ 99.9% → 早停，复用当前版本
                │   3. 对失败 module 调 module_optimizer
                │   4. verify_module_fix 判官：accept 才采纳，reject 保留旧 prompt
                │   5. composer 组装新版本（在 source 上继续 bump）
                │
                ▼
最终激活版本（source.prompt_version_id 指向它，工作区刷新即看到结果）
```

> **历史名词**：代码里仍出现 "fork" / `_fork_api_definition` / `CustomizeJobStatus.forking`
> 等用词，是 Phase 19 之前的命名保留。Phase 19 起这些 ≈ "在 source 上 bump 版本"，
> **不再创建新 ApiDef**，不再有 `-c1` api_code 后缀，不再切换工作区。

---

## 核心架构边界：国别层 vs 全球层（产品设计基石）

> 这是整个产品的分层基石，决定「加国家、做黄金集、改 prompt 机器」该往哪一层放。
> 一句话：**按国家分的是「知识与基准」，全球共享的是「契约与机器」**。
> 服务形态 = 「**全球机器 + 国别皮肤**」：加一个国家只写它的增量，机器改进全球复用。

### 为什么这样分

不存在一份覆盖全球的「识别准则」——语言、票面结构、税号/日期/货币规则因国而异。
但**「找到值之后怎么组装成合法 JSON」是 schema 级、语言无关的**，可以也应该全球统一。
所以：全球标准存在于**输出形状**，不存在于**输入识别**。

### 两层的归属（改动前先问：这属于哪一层？）

| 层 | 内容 | 归属 | 代码位置 |
|---|---|---|---|
| Part 1 国家事实 | 票据分类 / 语言 / 货币 / 日期 / 税号规则 | **按国家** | `<COUNTRY>_invoice_prompt.yaml` |
| Part 2 字段语义 | 每个字段「在哪找、找什么」 | **按国家** | 同上 |
| 反思领域知识 | SST/TIN/SSM、双货币取错等国别易错点 | **按国家** | `reflection/country_agents/<COUNTRY>/` |
| 黄金种子 | 验证用文档 + 人工核验 GT + 参考 prompt | **按国家** | `eval/golden_set/<COUNTRY>/` |
| Part 3 输出契约 | 去千分位 / qty×price 校验 / 一行一项 / 缺字段处理 | **全球统一** | `assets/global_output_contract.yaml` |
| 优化机器 | composer / FieldRule / reconciler / eval harness / 全局 skill | **全球共享** | `service/`、`reflection/skills/` |

### 准则（违反 = 架构漂移）

1. **国别知识只进国别层**：国家事实、字段语义、领域易错点、黄金集，一律按 `<COUNTRY>` 隔离；
   严禁把某国特例塞进 Part 3 平台契约或全局 skill。
2. **机器永远全球共享**：composer / FieldRule / reconciler / harness / 输出契约**不按国家分叉**。
   加国家 = 只写国别增量（Part1/2 + agent + 黄金集），不重建管线（呼应 skill-creator「公共基底 + 薄变体」）。
3. **分区主轴是国家，不是纯语种**：税号/日期/货币/税种是国家绑定；一国多语在该国 Part 1 内部消化；
   保留 `GLOBAL` 兜底模板应对跨国/未知票面。
4. **不过度细分**：比国家更细的差异（发行方/行业）交给**客户自己的 customize 回路**（用其样本拟合），
   不再单独建层。
5. **Phase 4 等机器改动是全球的，但验证逐国跑**：同一台机器在不同国别票上表现不同；
   先用 MY 黄金集验证 MY 不回退，其它国家各自黄金集就绪后再补验。

### 黄金种子 ≠ 客户运行时回路（务必分清）

- **客户回路**：国家模板（fork 起点）+ 客户已审视样本 GT + 客户修订 → 在**客户自己样本**上 3 轮迭代。
  **不碰黄金种子**。
- **黄金种子**：平台侧、偶发的回归/泛化护栏，只在动 composer/skill/reconciler 时跑 A/B，
  专抓「客户 3 样本测不出的过拟合/回归」。必须**平台所有且冻结**（不可用会变的客户实时样本）。

#### 黄金门槛 = 离线平台 CI（红线，违反 = 架构事故）

> 目的：守住「黄金回路绝不制约客户、绝不按客户数烧 token」。

1. **黄金门槛只在平台改机器时离线跑**（改 composer / skill / reconciler / 输出契约的 PR），
   跑在**冻结黄金集**上。**严禁**把黄金检查接进 `_run_one_round` / customize / 任何客户路径。
   —— 客户迭代时代码**永不调用**黄金门槛。
2. **黄金门槛 gate 的是「平台机器改动」，不是「客户 prompt」**。客户 prompt 过/不过，由**客户
   自己的样本（模糊评分）**决定，黄金集对它零影响。黄金门槛只能拦下一个平台 PR，拦不下客户迭代。
3. **两套评分分流，不互通**：客户迭代用**模糊**（容收敛）；黄金门槛用**零容差严格**（仅平台 A/B 比较两版机器）。
   严格标准**不得渗进客户回路**。
4. **门槛是相对「不回退」，不是绝对 100%**：机器改动只要黄金严格分**不下降**即放行，不要求满分。
5. **黄金回路的优化标的是国家规范模板**（golden_prompt / Part1/2 / 基础模块），
   **不是任何客户的 ApiDef**。它抬高所有客户的**起点** + 守机器；改客户 fork 的事归客户回路。

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

#### 准确率逐轮**单调不降**（客户升级回路的硬原则）

> 客户在已定制 api 上「加样本 + 改字段」再迭代升级时，**每一轮的整体准确率必须 ≥ 上一轮**；
> 绝不允许一轮"优化"把 prompt 改得更差还被激活。

- **门口评估即是单调判据**：round N+1 入口对 v(N) 跑 OCR+eval 得 `acc(v(N))`；
  与上一已激活版本的 `acc(prev)` 比较。
- **回退守护（硬保证）**：若 `acc(v(N)) < acc(prev)` → **丢弃 v(N)，保留 prev 为激活版本**，
  该轮记为"无提升"（不算 failed），accuracy 永不下降。
  —— `verify_module_fix` 的 per-module accept/reject 是**软**代理，不足以保证整体单调；
  必须再加这道**版本级**的 re-score + 回退（依赖 §④ 评分已修正为真实值，见下）。
- **前提：评分必须是真实值**：本守护建立在「GT 根对齐后 accuracy 测真」之上
  （`ground_truth.align_for_path`）。评分若是假信号（dict-GT vs `$[*].` 路径切成 None），
  单调判据无意义。
- **早停仍优先**：真实 `acc ≥ 0.999` 时早停复用，属合法的单调（持平）。
- **成本兜底不变**：max 3 轮 + 每轮只优化不达标 module；单调守护**不增加轮数**，只决定每轮产物是否采纳。

---

### ⑤ 反思必须按 skill 分流且可累积

**文件**: `backend/app/ocr_optimizer/reflection/`

#### 约定

1. **反思 skill 是 YAML 资产**，路径 `reflection/skills/*.yaml`，由产品技术维护（非客户面向）。
2. 一个 diff 可以匹配**多个** skill，每个 skill 各产出一段 fix_suggestion。
3. 在 `_fork_api_definition` 里，同一个 `module_key` 收到多条 diff 时**累积**所有 fix_suggestion / corrected_value 到 prompt 后缀，**不要后写覆盖**。
   - **跨轮矛盾消解（Phase 4，`service/reconciler.py`）**：当该字段 prompt **已含累积反馈**（`has_accumulated_feedback`）且本轮又有新 suggestion 时，调 `reconcile_module_prompt` 把累积 prompt + 新建议协调成**单一自洽** prompt，**冲突时以最新客户意图为准**；fail-open（LLM 失败回退到本条「累积追加」）。这与「累积不覆盖」不矛盾：累积是默认，**矛盾**时才协调。
   - reconciler 产出的是协调后的 **ocr_prompt**（保留识别要点），composer 仍渲染 ocr_prompt；**不**切到 FieldRule 骨架（骨架是 reconciler 的输入参考，不是渲染替代）。
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
