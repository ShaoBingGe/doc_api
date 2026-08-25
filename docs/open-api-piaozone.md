# 开放平台接口（piaozone 兼容层）

对齐生产日志切片 `1787497421902_振兴客户请求切片.md`。外部客户按本文调用，
路径、鉴权、入参、出参与线上生产**逐字段一致**。

与既有的 `/api/v1/extract/{api_code}`（`X-API-Key`）**并存**：后者供工作区/前端使用，
本文这套供外部客户使用。两者共用同一条提取管线（`extract_service`），
因此模板一次优化，两个入口同时生效。

---

## 一、获取 access_token

```
POST {base_url}/base/oauth/token
Content-Type: application/json
```

请求体：

```json
{
  "client_id": "TN_RACzZVvvVh7MHg2xT",
  "timestamp": "1755870022",
  "sign": "MD5(client_id + client_secret + timestamp)"
}
```

- `sign`：三段字符串**直接拼接**后取 MD5，小写十六进制。
- `timestamp`：unix 秒。服务端校验 ±15 分钟窗口（防重放）；传 13 位毫秒时间戳也会被自动换算。

成功返回：

```json
{
  "errcode": "0000",
  "description": "操作成功",
  "access_token": "759fbf829d528c94cb212450cb44efb4",
  "token_type": "bearer",
  "expires_in": 129600
}
```

`expires_in` 单位为秒（129600 = 36 小时）。**建议缓存复用，过期前重取**——
token 存于数据库，服务重启不失效。

---

## 二、文档结构化解析

```
POST {base_url}/ai/knowledge/nlpService/document/analyze?access_token={token}
client-platform: common
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `templateId` | 是 | 数字模板号，如 `7` |
| `file` | 是 | 文档文件（PDF / 图片） |
| `fileHash` | 否 | 文件 MD5；回填到响应的 `sourceFileHash` |
| `clientId` | 否 | 传了则必须与 access_token 所属 client 一致，否则拒绝 |

响应外壳：

```json
{
  "errcode": "0000",
  "description": "Success",
  "data": [ { "header": { ... }, "detail": { ... } } ],
  "traceId": "63ccb3eb4b1bc740",
  "docPages": 6
}
```

**HTTP 状态码恒为 200，成败看 `errcode`**（`"0000"` 为成功），与线上一致——
调用方无需对 HTTP 层做分支。

### data[] 结构

每个元素代表文档中的一张票据（混贴/多页文档会返回多个元素）：

- `header.basic` — 12 字段：`sourceFileHash` `docType` `nameOfInvoice` `invoiceType`
  `invoiceNumber` `invoiceCode` `invoiceDate` `totalNetAmount` `totalAmount`
  `totalTaxAmount` `currency` `page`
- `header.billTo` — 14 字段（收票方）
- `header.billFrom` — 14 字段（开票方，含 `billFromBusinessRegistrationNumber`）
- `header.bussiness` — 6 字段（**线上原样拼写，非笔误**）
- `header.payment` — 7 字段
- `detail.detailOfGoodsOrServices` — 商品明细，每行 14 字段
- `detail.detailOfTaxSummary` — 税金汇总，每行 4 字段
- `detail.originalInvoiceReferences` — 原发票引用

约定：

- **所有标量值都是字符串**（`"totalAmount": "100.24"`）；`page` 是字符串数组（`["1"]`）。
- **字段全集固定**：票面没有的字段返回空串 `""`，不省略键——调用方可无条件取值。


### 页数上限

单次调用最多处理 **16 页**（`qwen_processor.MAX_PAGES`）。超过部分**直接丢弃，不做分片再合并**
——分片会打断跨页票据的上下文且成本翻倍。

- `errcode` 仍为 `"0000"`（前 16 页确实识别成功了）
- `description` 变为 `"超过16页的部分，不予以识别"`
- `docPages` 报**原文档实际页数**，调用方据此可算出被截断了多少页

### 多票据文档

一份文档含多张彼此独立的票据时（如整月发票扫成一个 PDF），`data` 数组会返回**全部**票据，
每条带自己的 `page`。跨页的单张票据（后续页为 `Attachment` / `Page 2 of N` / 同一 Invoice No.）
则合并为一条，`page` 列出全部页码。

### 错误码

| errcode | 含义 |
|---|---|
| `0000` | 成功 |
| `4001` | client_id 无效或已停用 |
| `4002` | 签名不匹配 |
| `4003` | 时间戳超出窗口 |
| `4004` | access_token 无效 |
| `4005` | access_token 已过期 |
| `4006` | templateId 不存在 |
| `4007` | 无权使用该 templateId / clientId 与 token 不匹配 |
| `4008` | 缺少文件 |
| `5000` | 解析失败 |

失败响应保持同一外壳，`data` 为空数组。

---

## 二·B、异步识别（申请 + 轮询）

大文档识别可达数分钟，同步接口会把调用方的 HTTP 连接挂住。异步链路把它拆成
「立即拿 taskId」+「轮询取结果」两步。

```
POST /ai/knowledge/nlpService/document/analyze/async?access_token=<token>
POST /ai/knowledge/nlpService/tasks/query?access_token=<token>
```

### 与同步接口的三处差异（最容易写错的地方）

| | 同步 | 异步 |
|---|---|---|
| 错误码 | `4001`…`5000` | **A 系**：`A0301` / `A0410` / `A0426` / `A0700` / `C0110` / `1999` |
| 申请响应 | 有 `traceId`、`docPages`，`data` 是数组 | **无** `traceId` / `docPages`，`data` 是对象 |
| 结果字段 | `data[]` 是对象 | `result` 是**字符串**（JSON 文本，需自行 `json.loads`） |

两套错误码是对接方文档写死的，无法统一。异步响应额外带一个 `legacyErrcode` 字段，
是 A 系码到同步 4xxx 的对照，**仅供内部排查**，对接方不必解析。

### 申请

`multipart/form-data`：`file`（必填）、`templateId`、`fileHash`、`callbackUrl`。

```json
{"errcode":"0000","description":"成功","data":{"taskId":"d9318f61-…"},"legacyErrcode":"0000"}
```

> `callbackUrl` **当前只入库不触发回调**，请用轮询获取结果。回调（含 2/10/30 分钟
> 重试策略）计划在下一期实现。

### 查询

`application/json`：`{"taskIds": ["…"]}`，**最多 10 个**（超出返回 `A0426`）。

```json
{
  "errcode": "0000", "description": "成功", "traceId": "381dacadfb0793b1",
  "data": {
    "d9318f61-…": {
      "taskId": "d9318f61-…",
      "status": "COMPLETED",
      "statusDesc": "已完成",
      "requestParams": {"templateId": "7", "language": "auto", "fileName": "invoice.pdf"},
      "result": "{\"errcode\":\"0000\",\"data\":[…]}",
      "errorMessage": null
    }
  }
}
```

`status` 只有三个值：`PENDING` / `COMPLETED` / `FAILED`。
`result` 仅在 `COMPLETED` 时有值，其内容就是**同步接口的完整响应**（序列化成字符串）。
`errorMessage` 仅在 `FAILED` 时有值。

**查不到的 taskId 不出现在 `data` 里，也不报错** —— 不存在与「属于别的 client」
不作区分，避免泄露 taskId 是否存在。任务保留 10 天后自动清理。

### 异步错误码

| errcode | 含义 | legacyErrcode |
|---|---|---|
| `0000` | 成功 | `0000` |
| `A0301` | 访问未授权（token 无效/过期、无权使用该 templateId） | `4004` |
| `A0410` | 请求必填参数为空（无文件 / 缺 taskIds） | `4008` |
| `A0426` | 批量查询超过 10 个 | — |
| `A0700` | 用户上传文件异常（读取/落盘失败） | — |
| `C0110` | 识别管线出错 | `5000` |
| `1999` | 其他未归类失败 | `5000` |

---

## 二·C、并发与内存治理

单 worker 单体服务，**同步与异步共用一个准入闸**（`services/extract_gate.py`），
所以"全服务并发不超过 N"是一句真话，而不是两条路各自限流后相加。

闸有两个维度，同时满足才放行：

| 维度 | 默认 | 为什么 |
|---|---|---|
| 文档数 `GATE_MAX_DOCS` | 3 | 对接方要求 |
| 在途页数 `GATE_MAX_PAGES` | 24（阿里云）/ 18（腾讯云） | **实测每页渲染约占 30MB 内存**。只限文档数挡不住大文档：3 × 16 页 ≈ 1.4GB，直接撑爆 2G 的机器 |

其余要点：

- **排队不吃内存**：上传文件先落盘（`ASYNC_SPOOL_DIR`），队列里只有路径，
  一个排队任务约 200 字节。文件在**拿到槽位之后**才读进内存。
- **提取不阻塞事件循环**：`extract_document` 是同步函数，一律经
  `anyio.to_thread` 丢进线程池。直接在 `async` 路由里调用会独占事件循环整场识别
  （实测腾讯云 200 秒，期间取 token 会 30 秒超时）。
- **同步端点满载时**等槽位至多 `SYNC_GATE_WAIT_SEC`（默认 120s），
  超时返回 `5000` + "服务繁忙，请稍后重试"，不新增同步侧错误码。
- **重启恢复**：残留的 RUNNING 任务在启动时退回 PENDING（`recover_orphans`）。

满载实测（阿里云，6 个异步 + 1 个同步并发）：取 token 中位 10ms、最大 648ms、
**零次超过 1 秒**；内存 235MB → 峰值 465MB（上限 1500M）。

---

## 三、templateId 的分配

`templateId` 映射到 `api_definitions.external_template_id`（整数列，幂等补列）。
为空表示该 API 不对开放平台暴露。

**租户隔离**：模板挂了租户时，只有同租户的 client 能调用；
平台桶模板（`tenant_id` 为空）对所有已鉴权 client 开放。

### 已分配

| templateId | 客户 | 国家模板 | 说明 |
|---|---|---|---|
| 7 | Chinkin（振兴） | `MY_invoice_prompt.yaml` | 马来西亚票据，29 字段模块 |

开通新客户：

```bash
cd backend && python -m app.services.seed_open_api
```

脚本幂等：建租户 + client 凭证 + 从国家模板初始化 API 并打上 templateId。
`client_secret` 仅在首次创建时打印一次，重复执行不会重置。

---

## 四、调用示例

```bash
BASE=http://127.0.0.1:8000
CID=TN_RACzZVvvVh7MHg2xT
SECRET=<你的 client_secret>
TS=$(date +%s)
SIGN=$(printf "%s%s%s" "$CID" "$SECRET" "$TS" | md5 -q)   # Linux: md5sum | cut -d' ' -f1

TOKEN=$(curl -s -X POST "$BASE/base/oauth/token" \
  -H 'Content-Type: application/json' \
  -d "{\"client_id\":\"$CID\",\"timestamp\":\"$TS\",\"sign\":\"$SIGN\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -s -X POST "$BASE/ai/knowledge/nlpService/document/analyze?access_token=$TOKEN" \
  -H 'client-platform: common' \
  -F 'templateId=7' \
  -F "fileHash=$(md5 -q invoice.pdf)" \
  -F 'clientId=TN_RACzZVvvVh7MHg2xT' \
  -F 'file=@invoice.pdf'
```

---

## 五、代码位置

| 文件 | 职责 |
|---|---|
| `backend/app/api/v1/open_api.py` | 两个端点（挂在根路径，不带 `/api/v1`） |
| `backend/app/services/open_api_auth.py` | 签名校验、token 签发与解析、错误码 |
| `backend/app/services/open_api_mapper.py` | 扁平提取结果 → header/detail 分组 + 全字符串化 |
| `backend/app/models/open_api_client.py` | `OpenApiClient` / `OpenApiToken` |
| `backend/app/services/seed_open_api.py` | 客户开通种子脚本（幂等） |
| `backend/tests/test_open_api_piaozone.py` | 契约测试（12 项，零 token 消耗） |
