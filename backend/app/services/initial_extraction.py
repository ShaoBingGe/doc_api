"""
Initial OCR extraction — one-shot call with a canonical hierarchical prompt.

Used the very first time a brand-new API definition is created and a document
is uploaded with no fields detected yet. The prompt is intentionally generic
so it works on any invoice/receipt/bill without prior knowledge.

The output JSON mirrors the document's visual hierarchy:
  - leaf fields → { value, confidence, bbox }
  - container nodes (groups, tables, table rows) → carry a _meta.bbox that
    fully encloses every child's bbox.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.schemas.document import ProcessingResultResponse, ReprocessRequest
from app.services import document_service

logger = logging.getLogger(__name__)


INITIAL_INVOICE_EXTRACTION_PROMPT = """你是一名严谨的票据/发票信息抽取专家。请阅读这张文档(图片或PDF)\
并仅输出一份合法的 JSON,不要任何 markdown 代码块、解释、或多余文字。

# 一、整体目标
识别文档中所有有意义的字段(key/label)和每一张表(明细/税目/小计等),按文档真实的\
视觉层次输出嵌套 JSON。同一分组内的字段必须放进同一个嵌套对象;同一行重复的多条记\
录必须放进数组;数组元素本身是对象;表内还可以嵌套表。

# 二、叶子字段的固定格式
每个标量字段输出为一个对象,字段名作为父对象的 key:
{
  "value":      <字符串|数字|布尔|null>,
  "confidence": <0.0 - 1.0 的浮点数>,
  "bbox":       { "x": <0-100>, "y": <0-100>, "width": <0-100>, "height": <0-100>, "page": <从 1 开始的整数> }
}
约定:
  - 坐标一律为相对页面尺寸的百分比 (0-100),浮点数。
  - 找不到的字段: value=null, confidence=0, bbox=null。
  - 数字去掉千分位逗号和货币符号;日期统一 YYYY-MM-DD。
  - 不要编造文档里没有的字段。

# 三、容器节点的固定格式
任何容器(嵌套对象、数组、数组元素对象)都必须额外提供一个 "_meta" 字段,其中含一\
个 bbox,且该 bbox 必须完全包住该容器内所有子节点的 bbox(含跨行/跨列):
- 嵌套对象:
  {
    "_meta": { "bbox": {...} },
    "<子字段1>": { "value": ..., "confidence": ..., "bbox": ... },
    "<子字段2>": { ... }
  }
- 数组(表)用以下形态表达,以便携带 _meta:
  {
    "_meta": { "bbox": {...} },     // 整张表的外框
    "rows": [
      {
        "_meta": { "bbox": {...} }, // 单行的外框
        "<列1>": { "value": ..., "confidence": ..., "bbox": ... },
        "<列2>": { ... }
      },
      { ... }
    ]
  }
  即便表只有 1 行也保持此形态。

# 四、命名规范
- 顶层 key 使用 snake_case 英文(invoice_number, invoice_date, total_amount,\
  seller, buyer, line_items, taxes 等)。
- 表的 key 使用复数 (line_items, sub_taxes)。
- 嵌套对象的子字段同样 snake_case。

# 五、典型字段(若存在则尽量覆盖,不存在不要编造)
顶层标量: invoice_number, invoice_date, due_date, currency, sub_total,\
  tax_total, total_amount, payment_terms, po_number。
顶层容器对象: seller(含 name, tax_id, address, phone, email...),\
  buyer(同上), bank_info(含 bank_name, account_no, swift...)。
顶层表: line_items(每行含 description, quantity, unit_price, total_price,\
  tax_amount, tax_rate);taxes(每行含 tax_name, taxable_amount, tax_rate, tax_amount)。

# 六、自检清单
输出前自检:
1. 每个叶子是否都包含 value/confidence/bbox 三个键。
2. 每个容器(对象、数组对象、行)是否都带 _meta.bbox。
3. 每个容器的 bbox 是否真的包住了它所有子节点的 bbox。
4. 是否为合法 JSON,没有任何额外文字。
"""


def trigger_initial_extraction(
    db: Session,
    document_id: uuid.UUID,
) -> ProcessingResultResponse:
    """
    One-shot extraction using the canonical hierarchical prompt.

    Reuses document_service.reprocess_document so we get the same versioning,
    error handling, and DB persistence as the regular reprocess flow.
    """
    logger.info("Initial extraction triggered for document %s", document_id)
    body = ReprocessRequest(prompt=INITIAL_INVOICE_EXTRACTION_PROMPT)
    return document_service.reprocess_document(db, document_id, body)
