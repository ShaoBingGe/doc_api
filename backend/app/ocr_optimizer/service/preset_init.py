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
from .composer import GLOBAL_OUTPUT_CONTRACT_DETAILS

logger = logging.getLogger(__name__)


def init_from_country_template(
    db: Session,
    country: str,
    user_id: uuid.UUID | None = None,
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

    api_def = ApiDefinition(
        user_id=user_id,
        name=name,
        api_code=api_code,
        description=f"Placeholder API created from {country} preset template",
        status=ApiDefinitionStatus.pending_first_doc.value,
        version=1,
        response_schema=decomposed["json_schema"],
        processor_type="gemini",
        model_name="gemini-2.5-flash",
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
    v1_prompt = (
        decomposed["prompt_format"].rstrip()
        + "\n\n"
        + GLOBAL_OUTPUT_CONTRACT_DETAILS
        + "\n"
    )

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
