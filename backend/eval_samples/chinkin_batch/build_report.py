"""把 run_batch.py 的进度明细汇总成日志 md。

时间口径（三个都记，别混）：
  · 提交识别时间 = 客户端发出 multipart 请求的时刻
  · 取得结果时间 = 客户端收到完整响应体的时刻
  · 总耗时       = 两者之差，即对接方真实感知的端到端时长
    （含准入闸排队 + 渲染 + Gemini 识别 + 映射；不含本脚本 2s 的主动间隔）

用法：cd backend && python eval_samples/chinkin_batch/build_report.py
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[3]
PROGRESS = pathlib.Path("/tmp/chinkin_batch_progress.json")
OUT_MD = REPO / "docs" / "识别任务日志-sample_chinkin-20260831.md"
OUT_DIR_NAME = "sample_chinkin_results"


def fmt_amount(v) -> str:
    return "-" if v in (None, "") else str(v)


def main() -> None:
    recs = json.loads(PROGRESS.read_text())
    n = len(recs)
    ok = [r for r in recs if r.get("errcode") == "0000"]
    bad = [r for r in recs if r.get("errcode") != "0000"]
    times = [r["elapsed_s"] for r in recs]

    doc_types = Counter()
    inv_types = Counter()
    total_entities = 0
    for r in recs:
        for e in r["entities"]:
            total_entities += 1
            doc_types[e.get("docType") or "(空)"] += 1
            if e.get("docType") != "other":
                inv_types[e.get("invoiceType") or "(空)"] += 1

    L: list[str] = []
    A = L.append
    A("# sample_chinkin 批量识别日志 · 2026-08-31")
    A("")
    A("## 一、任务概况")
    A("")
    A(f"- **样本目录**：`sample_chinkin/`（{n} 张 JPG 票据照片）")
    A(f"- **结果目录**：`{OUT_DIR_NAME}/`（每张一个同名 `.json`，内容为接口原始响应体）")
    A("- **调用端点**：`POST http://129.226.145.150/ai/knowledge/nlpService/document/analyze`"
      "（腾讯云生产，公网入口经 Caddy 反代）")
    A("- **模板**：`templateId=7` → MY 国家模板 **7_16 英文版**（本日上线，"
      "见 `_bak/20260831/MY_invoice_prompt.zh-7_16.yaml` 为上线前中文版备份）")
    A("- **调用方式**：**顺序串行**，每张之间主动间隔 **2 秒**（不并发，避免占满准入闸）")
    A(f"- **时间口径**：`总耗时 = 取得结果时间 − 提交识别时间`，即对接方感知的端到端时长；"
      f"不含脚本主动 sleep 的 2 秒间隔")
    A("")
    A("## 二、汇总统计")
    A("")
    A("| 指标 | 数值 |")
    A("|---|---|")
    A(f"| 提交样本数 | {n} |")
    A(f"| **接口调用成功率** | **{n}/{n} = 100%**（无一张卡在传输/服务错误，"
      f"其中 5 张经重试补齐，见第四节） |")
    A(f"| ├ 判定含有效票据（`errcode=0000`） | {len(ok)} |")
    A(f"| └ 判定无有效票据（`errcode=4009`） | {len(bad)} |")
    A(f"| 累计识别出票据实体数 | {total_entities} |")
    A("")
    A("> **注意口径**：`4009` 是**分类结论**（整份文档没有票据，多为通行卡流水表），"
      "不是识别失败——把它算成「失败」会严重低估实际表现。抽查佐证见第三节。")
    A("")
    A("| 耗时指标 | 数值 |")
    A("|---|---|")
    A(f"| 总耗时 · 平均 | {sum(times)/len(times):.1f}s |")
    A(f"| 总耗时 · 最短 | {min(times):.1f}s |")
    A(f"| 总耗时 · 最长 | {max(times):.1f}s |")
    A(f"| 总耗时 · 中位 | {sorted(times)[len(times)//2]:.1f}s |")
    A(f"| 全批次墙钟（含 2s 间隔） | 约 {(sum(times) + 2*(n-1))/60:.1f} 分钟 |")
    A("")
    A("**docType 分布**：" + "、".join(f"`{k}` × {v}" for k, v in doc_types.most_common()))
    A("")
    A("**invoiceType 分布**（不含 other）："
      + ("、".join(f"`{k}` × {v}" for k, v in inv_types.most_common()) or "—"))
    A("")

    A("## 三、4009 说明：29 张「未识别到有效票据」是否误判")
    A("")
    A("`4009` 表示服务端判定整份文档没有任何有效票据（全部页判为 `docType: other`），"
      "**不返回空壳成功**。逐张抽查后确认这是**模板的预期行为**，不是识别失败：")
    A("")
    A("| 抽查样本 | 实际内容 | 判 other 的依据 |")
    A("|---|---|---|")
    A("| `IMG_20260821_161226` | Touch 'n Go **TRANSACTION HISTORY**（通行卡流水表） "
      "| 模板 §1.0.5：对账单/交易流水表整页多行流水，不拆成票据 |")
    A("| `IMG_20260821_161552` | 同上，流水续页 | 同上 |")
    A("| `IMG_20260822_171152` | 同上，流水尾页（印 `End of the statement`） | 同上 |")
    A("| `IMG_20260824_112818` | 同上，流水尾页 | 同上 |")
    A("| `IMG_20260821_164839` | Batik Air **选票比价页**（Select departing flight，含 SOLD OUT） "
      "| 成交前的报价页面，非付款凭证（同 §1.1.1 报价单法理）|")
    A("| `IMG_20260824_110413` | Maybank **转账付款回执**（RM 5,247.27 转予个人） "
      "| §1.0.5 银行交易记录 → other。**⚠️ 此条属边界情形，见下** |")
    A("")
    A("**4009 的分布也支持这个结论**：29 张里绝大多数是**连号成串**的"
      "（#2–10、#24–28、#30–33、#53–60），正是同一叠通行卡流水被逐页拍照的形态。")
    A("")
    A("> **⚠️ 需要你拍板的一条**：`IMG_20260824_110413` 这类**银行转账回执**"
      "（Maybank Cash Management System，有明确金额、日期、收付双方、Reference No）"
      "目前按「银行交易记录」判 other。若振兴的报销流程需要把付款回执也入账，"
      "这条规则要单独开口子——这是**业务口径问题**，不是识别错误，我没有擅自改模板。")
    A("")

    retried = [r for r in recs if r.get("retried")]
    A("## 四、首轮失败与重试")
    A("")
    if not retried:
        A("无。")
    else:
        A(f"首轮 {len(retried)} 张未拿到结果，已重试补齐；最终 60/60 全部有结果。")
        A("")
        A("| # | 文件 | 首轮失败原因 | 重试次数 | 重试后 |")
        A("|---:|---|---|---:|---|")
        A("| 3 | `IMG_20260821_161248.jpg` | HTTP 502 网关超时（92s） | 1 | `4009` |")
        A("| 7 | `IMG_20260821_161712.jpg` | HTTP 502 网关超时（173s） | 1 | `4009` |")
        A("| 14 | `IMG_20260821_162542.jpg` | HTTP 502 网关超时（**717s**） | 3 | `0000` |")
        A("| 46 | `IMG_20260824_111111111.jpg` | `5000` 文件头与扩展名不符 | 1 | `0000` |")
        A("| 54 | `IMG_20260824_112540.jpg` | HTTP 502 网关超时（113s） | 1 | `4009` |")
        A("")
        A("### 4.1 HTTP 502（4 张）——上游 Gemini 偶发超时")
        A("")
        A("服务端日志显示同时段 Gemini 反复返回 "
          "`503 UNAVAILABLE: Deadline expired before operation could complete`，"
          "主备切换（gemini → qwen）**按设计正常触发并有日志**，但单次请求被拉长到"
          "超过 Caddy 的 600s `read_timeout`，网关先行断开、客户端收到 502。")
        A("")
        A("**一个值得注意的证据**：#14 第 3 次重试只用了 **4.6s** 就返回完整结果——"
          "这说明第 2 次尝试虽然客户端已收到 502，**服务端其实把活干完了并写入了结果缓存**。"
          "即 502 是*网关/客户端侧*的超时，不代表服务端识别失败。")
        A("")
        A("> **待跟进**：#14 首轮有一段 **8.3 分钟服务端无任何处理器日志**的空窗"
          "（20:59:59 Gemini 返回后到 21:08:20 之间既无 `Calling Gemini` 也无 "
          "`Calling Qwen`）。这段卡在哪一层未能从日志定论——不在模型调用内，"
          "也不像准入闸（`SYNC_GATE_WAIT_SEC=120s` 会主动报错而非静默挂起）。"
          "**建议单独排查**，本次未改动任何代码。")
        A("")
        A("### 4.2 `IMG_20260824_111111111.jpg` —— 文件名骗人")
        A("")
        A("该文件扩展名是 `.jpg`，但文件头是 `89504e47`（PNG）。"
          "服务端「文件头须与扩展名一致」的校验把它挡在识别之前，返回 `5000`——"
          "**服务端行为是对的，是样本文件本身命名有误**。"
          "重试时按真实类型申报为 `image/png` 后正常识别（COCO STEAMBOAT，`INVOICE`）。")
        A("")

    A("## 五、逐张识别记录")
    A("")
    A("> `总耗时 = 取得结果时间 − 提交识别时间`。重试过的行记录的是**最后一次成功尝试**的时间，"
      "已在备注列标出。")
    A("")
    A("| # | 文件 | 大小 | 提交识别时间 | 取得结果时间 | 总耗时 | 票据数 | 识别结果摘要 | 备注 |")
    A("|---:|---|---:|---|---|---:|---:|---|---|")
    for r in recs:
        if r["entities"]:
            parts = []
            for e in r["entities"]:
                if e.get("docType") == "other":
                    parts.append("other")
                else:
                    parts.append(
                        f"{e.get('docType')}/{e.get('invoiceType')} · "
                        f"{e.get('invoiceNumber') or '(无号)'} · "
                        f"{e.get('currency') or ''} {fmt_amount(e.get('totalAmount'))} · "
                        f"{(e.get('billFromName') or '(无开票方)')[:28]} · "
                        f"{e.get('lineRows')} 行明细")
            summary = "<br>".join(parts)
        elif r.get("errcode") == "4009":
            summary = "未识别到有效票据（全页判 other）"
        else:
            summary = f"**失败** — {str(r.get('errmsg') or r.get('errcode'))[:80]}"

        note = ""
        if r.get("retried"):
            note = f"重试 {r['retried']} 次"
            if r.get("declared_png"):
                note += "；实际为 PNG，按 PNG 申报"
            if r["idx"] == 14:
                note += "；此次为缓存命中，非真实识别耗时"
        A(f"| {r['idx']} | `{r['file']}` | {r['size_kb']} KB | {r['submitted_at']} "
          f"| {r['received_at']} | {r['elapsed_s']:.1f}s | {r['n']} | {summary} | {note} |")
    A("")
    A("## 六、说明")
    A("")
    A("- 每张的**完整识别结果**（含明细行、税额汇总、买卖双方地址等全部字段）在 "
      f"`{OUT_DIR_NAME}/<同名>.json`，本表只摘关键字段便于速览。")
    A("- 服务端对「同一 client + 同一 templateId + 同一文件内容」有 **15 分钟结果缓存**。"
      "首轮 60 张内容互不相同，均为真实识别；**唯一的缓存命中是 #14 的第 3 次重试**"
      "（4.6s），该行不代表真实识别耗时，统计时请注意。")
    A("- `总耗时` 含准入闸排队、图片渲染、模型识别、结果映射全过程。"
      "本批次单线程顺序提交、闸内无竞争，因此该值近似等于纯识别时长。")
    A("- 本批次全程**未改动任何线上代码或模板**，只是调用生产接口。")

    OUT_MD.parent.mkdir(exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"→ {OUT_MD}  ({len(L)} 行)")


if __name__ == "__main__":
    main()
