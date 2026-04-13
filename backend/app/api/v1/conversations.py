"""
Conversation correction endpoints — NOT YET DEVELOPED.

对话矫正 API 暂未实现，本文件为占位符。
所有端点返回 503 Service Unavailable。

计划端点（P4 阶段实现）：
  POST   /api/v1/conversations                        创建对话
  GET    /api/v1/conversations                        对话列表
  GET    /api/v1/conversations/{id}                   对话详情（含消息历史）
  POST   /api/v1/conversations/{id}/messages          发送矫正消息（SSE）
  GET    /api/v1/conversations/{id}/schema            当前 Schema
  PUT    /api/v1/conversations/{id}/schema            手动修改 Schema
  GET    /api/v1/conversations/{id}/schema/history    Schema 版本历史
  POST   /api/v1/conversations/{id}/rollback/{ver}    回滚到指定版本
  DELETE /api/v1/conversations/{id}                   删除对话
"""

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/conversations", tags=["Conversations (TODO)"])

_NOT_IMPLEMENTED = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Conversation API is not yet implemented (planned for P4).",
)


# ── All routes return 503 until P4 ───────────────────────────────────────────

@router.post("", include_in_schema=False)
async def _create():
    raise _NOT_IMPLEMENTED


@router.get("", include_in_schema=False)
async def _list():
    raise _NOT_IMPLEMENTED


@router.get("/{conversation_id}", include_in_schema=False)
async def _get(conversation_id: str):
    raise _NOT_IMPLEMENTED


@router.post("/{conversation_id}/messages", include_in_schema=False)
async def _send_message(conversation_id: str):
    raise _NOT_IMPLEMENTED


@router.get("/{conversation_id}/schema", include_in_schema=False)
async def _get_schema(conversation_id: str):
    raise _NOT_IMPLEMENTED


@router.put("/{conversation_id}/schema", include_in_schema=False)
async def _update_schema(conversation_id: str):
    raise _NOT_IMPLEMENTED


@router.get("/{conversation_id}/schema/history", include_in_schema=False)
async def _schema_history(conversation_id: str):
    raise _NOT_IMPLEMENTED


@router.post("/{conversation_id}/rollback/{version}", include_in_schema=False)
async def _rollback(conversation_id: str, version: int):
    raise _NOT_IMPLEMENTED


@router.delete("/{conversation_id}", include_in_schema=False)
async def _delete(conversation_id: str):
    raise _NOT_IMPLEMENTED
