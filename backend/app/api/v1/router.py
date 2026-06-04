"""
v1 API router — aggregates all sub-routers.

挂载规则：
  /api/v1/documents/...              文档管理
  /api/v1/documents/{id}/annotations 标注管理（嵌套在 documents 下）
  /api/v1/conversations/...          对话矫正（stub，P4 实现）
  /api/v1/api-definitions/...        API 定义管理
  /api/v1/api-keys/...               API Key 管理
  /api/v1/extract/...                公有云提取端点
"""

from fastapi import APIRouter

from .admin_users import router as admin_users_router
from .annotations import router as annotations_router
from .api_defs import router as api_defs_router
from .api_keys import router as api_keys_router
from .auth import router as auth_router
from .conversations import router as conversations_router
from .country_templates import router as country_templates_router
from .documents import router as documents_router
from .extract import router as extract_router
from .reflection_agents import router as reflection_agents_router
from app.ocr_optimizer.router import router as ocr_optimizer_router
from .templates import router as templates_router
from .tenant_users import router as tenant_users_router
from .usage import router as usage_router

v1_router = APIRouter(prefix="/api/v1")

# auth & user/role management
v1_router.include_router(auth_router)
v1_router.include_router(admin_users_router)
v1_router.include_router(tenant_users_router)

v1_router.include_router(documents_router)
v1_router.include_router(annotations_router)
v1_router.include_router(conversations_router)
v1_router.include_router(api_defs_router)
v1_router.include_router(api_keys_router)
v1_router.include_router(extract_router)
v1_router.include_router(ocr_optimizer_router)
v1_router.include_router(templates_router)
v1_router.include_router(country_templates_router)
v1_router.include_router(reflection_agents_router)
v1_router.include_router(usage_router)
