"""
Preset country-template initialization orchestrator.

One-shot: given a country code, create a placeholder ApiDefinition + the
first OcrPromptVersion + all decomposed OcrModules in a single transaction.
This is the §6.4 entry point invoked by `POST /api/v1/api-definitions/from-country-template`.

The version's `composed_prompt` is stored as the **raw yaml prompt_format**
(with placeholders replaced) — NOT the composer's assembled output. This is
the deliberate v1 special case documented in design §5.2 / §6.4 / §10. From
v2 onwards (after the first `advance_round`), composer assembles modules
normally.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models.api_definition import ApiDefinition, ApiDefinitionStatus

from ..models import (
    OcrModule,
    OcrPromptVersion,
    PromptVersionStatus,
    VersionOrigin,
)
from . import template_loader
from .composer import GLOBAL_OUTPUT_CONTRACT_DETAILS, _render_schema_tree

logger = logging.getLogger(__name__)


def _render_field_contract(schema: dict) -> str:
    """渲染「字段键名清单」段，供 v1 prompt 使用。

    为什么必须有：qwen 视觉模型不支持 response_schema 硬约束（DashScope 限制），
    Gemini 虽支持但 extract 链路当前也未传 runtime_config——两条链路的字段键名
    实际都只靠 prompt 文本约束。没有清单时模型会自行起名，实测漂移包括
    date/issueDate（应为 invoiceDate）、subTotal（应为 totalNetAmount），
    甚至把 totalAmount 留空而只填 grossAmount。

    只渲染「路径 · 类型 · enum」的紧凑树（约为 JSON dump 的 20%）；字段的业务
    语义由 Part 1/2 的国家规则承担，此处不重复 description。
    """
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "# 输出字段清单（键名必须逐字一致）\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "最终 JSON **只能使用下列键名**，严禁自创、改写、缩写或翻译键名。\n"
        "常见错误：把 `invoiceDate` 写成 `date`/`issueDate`；把 `totalNetAmount`\n"
        "写成 `subTotal`；把 `billFromName` 写成 `billFrom`；把金额只填进\n"
        "`grossAmount` 而让 `totalAmount` 留空。**读对了值但填错键 = 该字段丢失。**\n"
        "票面没有的字段不输出该键（不要给 null / \"\" / 0），但**凡是读到的值，"
        "必须放进下列对应的键**。\n\n"
        f"{_render_schema_tree(schema)}\n"
    )


def build_v1_prompt(decomposed: dict) -> str:
    """组装 v1 版本的 composed_prompt = 国家规则 + 字段清单 + Part 3 输出契约。

    抽成函数供两处复用：建 API 时（init_from_country_template）与平台升级国家模板
    后的存量刷新（seed_open_api.refresh_prompt_from_country_template）。两边若各写
    一份，刷新就会把字段清单段洗掉。
    """
    return (
        decomposed["prompt_format"].rstrip()
        + "\n\n"
        + _render_field_contract(decomposed["json_schema"])
        + "\n"
        + GLOBAL_OUTPUT_CONTRACT_DETAILS
        + "\n"
    )


def init_from_country_template(
    db: Session,
    country: str,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> dict:
    """Create placeholder ApiDef + v1 + 30 modules atomically.

    Returns: {api_definition_id, version_id, redirect_url}
    """
    country = (country or "").upper()
    if not country:
        raise ValidationError("country is required")

    # Load and decompose the yaml (raises FileNotFoundError if missing)
    try:
        decomposed = template_loader.decompose_country_template(country)
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc))

    short_hex = uuid.uuid4().hex[:6]
    name = f"{country}_invoice_{short_hex}"
    api_code = f"{country.lower()}-invoice-{short_hex}"

    # Production OCR processor follows the deployment config (DEFAULT_PROCESSOR):
    # gemini (overseas) / qwen (大陆云) / mock (dev). model_name is informational —
    # each processor resolves its own model and ignores foreign names.
    from app.core.config import get_settings
    _s = get_settings()
    _proc = _s.DEFAULT_PROCESSOR or "gemini"
    _model = {"qwen": _s.QWEN_MODEL, "gemini": _s.GEMINI_MODEL}.get(_proc, _proc)

    api_def = ApiDefinition(
        user_id=user_id,
        tenant_id=tenant_id,
        name=name,
        api_code=api_code,
        description=f"Placeholder API created from {country} preset template",
        status=ApiDefinitionStatus.pending_first_doc.value,
        version=1,
        response_schema=decomposed["json_schema"],
        processor_type=_proc,
        model_name=_model,
        config={
            "source_country": country,
            "preset_yaml": f"{country}_invoice_prompt.yaml",
            "preset_yaml_id": decomposed.get("yaml_id"),
        },
    )
    db.add(api_def)
    db.flush()  # populate api_def.id

    # v1 special case (design §5.2 / §6.4): composed_prompt is the raw yaml
    # prompt_format with placeholders replaced. From v2 onwards composer.assemble_prompt
    # is used and includes Part 3 (GLOBAL_OUTPUT_CONTRACT_DETAILS) automatically.
    # For v1 we MUST append Part 3 here too so the platform output contract is
    # enforced from the very first OCR call (design v7).
    #
    # 字段清单同样必须进 v1（原先没有，是字段名漂移的根因）：qwen 视觉模型不支持
    # response_schema 硬约束，字段键名完全靠 prompt 文本约束。缺清单时模型自行起名——
    # 实测同一份 MY 模板下 invoiceDate 被写成 date、totalNetAmount 写成 subTotal、
    # totalAmount 干脆留空（值读对了却填错键），下游取不到数。
    v1_prompt = build_v1_prompt(decomposed)

    version_id = uuid.uuid4()
    version = OcrPromptVersion(
        id=version_id,
        api_definition_id=api_def.id,
        version="1",
        status=PromptVersionStatus.active.value,
        origin=VersionOrigin.init.value,
        composed_prompt=v1_prompt,
        composed_schema=decomposed["json_schema"],
        country_global_text=decomposed["country_global_text"],
        notes=f"Initial version from preset template {country}_invoice_prompt.yaml",
        activated_at=datetime.now(timezone.utc),
    )
    db.add(version)

    for spec in decomposed["modules"]:
        db.add(
            OcrModule(
                id=uuid.uuid4(),
                prompt_version_id=version_id,
                module_key=spec["module_key"],
                display_name=spec["display_name"],
                description=spec["description"],
                json_path=spec["json_path"],
                schema_fragment=spec["schema_fragment"],
                ocr_suggestions=spec["ocr_suggestions"],
                ocr_prompt=spec["ocr_prompt"],
                order_index=spec["order_index"],
            )
        )

    api_def.prompt_version_id = version_id

    db.commit()
    db.refresh(api_def)

    logger.info(
        "Created placeholder ApiDefinition %s (api_code=%s) from %s template with %d modules",
        api_def.id,
        api_def.api_code,
        country,
        len(decomposed["modules"]),
    )

    return {
        "api_definition_id": str(api_def.id),
        "version_id": str(version_id),
        "redirect_url": f"/workspace/api/{api_def.id}",
        "module_count": len(decomposed["modules"]),
    }
