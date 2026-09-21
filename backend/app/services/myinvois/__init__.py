"""MyInvois (LHDN 马来西亚税局) 查验增强子系统。

在 OCR 之外并行跑一条「官方取数」管线，用税局原始数据校正 OCR 结果：

    PDF ──link_extractor──► [MyInvoisRef(uuid, share_token, page)]   ← 零 OCR
                                   │
                                   ▼  client.fetch_raw(uuid)  (需 access_token)
                            LHDN /raw 响应（含 base64 UBL 2.1 JSON/XML）
                                   │
                                   ▼  ubl_mapper.map_document()
                            标准 29 字段结构（与国家模板同构）
                                   │
                                   ▼  fusion.fuse_entities()
                            消融归一结果 + 逐字段 provenance

设计要点（对应工程红线）：
  • 全链路 **fail-open**：任何一步失败都不得影响 OCR 结果返回（§五 降级而非 500）。
  • 不新增输出字段：provenance 走 ExtractResponse.metadata，不污染 29 字段契约（§①）。
  • access_token 由外部定期推送（见 api/v1/myinvois.py），本服务只读不签发。
"""

from .enricher import MyInvoisLookup, enrich_and_fuse, fuse_lookup, lookup_myinvois
from .fusion import FusionReport, fuse_entities, fuse_sources
from .gateway_client import MyInvoisGateway, VerifyResult
from .link_extractor import MyInvoisRef, extract_refs
from .qr_extractor import QrHit, extract_qr_hits
from .ubl_mapper import map_document

__all__ = [
    "MyInvoisRef",
    "extract_refs",
    "map_document",
    "fuse_entities",
    "FusionReport",
    "enrich_and_fuse",
    "lookup_myinvois",
    "fuse_lookup",
    "MyInvoisLookup",
    "MyInvoisGateway",
    "VerifyResult",
    "QrHit",
    "extract_qr_hits",
    "fuse_sources",
]
