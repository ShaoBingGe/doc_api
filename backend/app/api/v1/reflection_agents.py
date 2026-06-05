"""
Country-specific reflection agent admin endpoints.

These let product/tech operators view and update the per-country
add_field / edit_field reflection agents from the workspace UI's
"迭代过程" tab. NOT customer-facing — agents apply globally to ALL of
that country's customer iterations.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.core.deps import require_roles
from app.models.user import UserRole
from app.ocr_optimizer.reflection import country_agents_loader as cal

# Country reflection agents are platform knowledge — only platform admins.
router = APIRouter(
    tags=["Reflection Agents"],
    dependencies=[Depends(require_roles(UserRole.super_admin, UserRole.system_admin))],
)


class AgentResponse(BaseModel):
    key: str
    display_name: str
    country: str
    kind: str
    version: int
    remark: str
    system_prompt: str
    user_prompt_template: str


class AgentSaveRequest(BaseModel):
    display_name: str | None = None
    remark: str | None = None
    system_prompt: str
    user_prompt_template: str


@router.get(
    "/reflection-agents/countries",
    summary="列出已有反思 agent 配置的国家",
)
def list_countries() -> list[str]:
    return cal.list_countries()


@router.get(
    "/reflection-agents/{country}",
    summary="返回该国家的两个反思 agent（add_field / edit_field）",
)
def get_agents(country: str) -> dict[str, AgentResponse | None]:
    agents = cal.load_country_agents(country)
    return {
        "add": AgentResponse(**agents["add"].to_dict()) if "add" in agents else None,
        "edit": AgentResponse(**agents["edit"].to_dict()) if "edit" in agents else None,
    }


@router.put(
    "/reflection-agents/{country}/{kind}",
    summary="保存该国家某种 agent；自动 bump version",
)
def save_agent(
    country: str,
    kind: str,
    body: AgentSaveRequest,
) -> AgentResponse:
    if kind not in cal.VALID_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind must be one of {cal.VALID_KINDS}, got {kind!r}",
        )
    saved = cal.save_country_agent(
        country=country,
        kind=kind,
        display_name=body.display_name,
        remark=body.remark,
        system_prompt=body.system_prompt,
        user_prompt_template=body.user_prompt_template,
    )
    return AgentResponse(**saved.to_dict())
