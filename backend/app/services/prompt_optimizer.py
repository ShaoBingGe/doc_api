"""
Prompt optimization service.

Iteratively refines extraction prompts based on user correction feedback.
Algorithm:
  1. Collect all is_corrected=True annotations for the API's sample document
  2. Take current active prompt as base
  3. Build meta-prompt: "Here's the current prompt + correction examples, improve it"
  4. Call LLM to generate improved prompt
  5. Evaluate new prompt on sample document against corrections
  6. If accuracy improves → save as new version
  7. Repeat up to MAX_ROUNDS or until target accuracy reached
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationError
from app.models.annotation import Annotation
from app.models.api_definition import ApiDefinition
from app.models.document import Document, ProcessingResult
from app.models.prompt_version import PromptVersion

logger = logging.getLogger(__name__)

MAX_ROUNDS = 5
TARGET_ACCURACY = 0.95


def get_active_prompt(db: Session, api_definition_id: uuid.UUID) -> str | None:
    """Get the active prompt text for an API definition, or None."""
    version = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.api_definition_id == api_definition_id,
            PromptVersion.is_active == True,  # noqa: E712
        )
        .first()
    )
    return version.prompt_text if version else None


def list_versions(db: Session, api_definition_id: uuid.UUID) -> list[dict[str, Any]]:
    """List all prompt versions for an API definition."""
    versions = (
        db.query(PromptVersion)
        .filter(PromptVersion.api_definition_id == api_definition_id)
        .order_by(desc(PromptVersion.version))
        .all()
    )
    return [
        {
            "id": str(v.id),
            "version": v.version,
            "accuracy_score": v.accuracy_score,
            "is_active": v.is_active,
            "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "prompt_preview": v.prompt_text[:200] + ("..." if len(v.prompt_text) > 200 else ""),
        }
        for v in versions
    ]


def activate_version(
    db: Session, api_definition_id: uuid.UUID, version_id: uuid.UUID
) -> dict[str, Any]:
    """Activate a specific prompt version (deactivating others)."""
    target = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.id == version_id,
            PromptVersion.api_definition_id == api_definition_id,
        )
        .first()
    )
    if not target:
        raise NotFoundError(f"PromptVersion {version_id} not found")

    # Deactivate all others
    db.query(PromptVersion).filter(
        PromptVersion.api_definition_id == api_definition_id,
        PromptVersion.is_active == True,  # noqa: E712
    ).update({"is_active": False})

    target.is_active = True
    db.commit()
    db.refresh(target)

    return {
        "id": str(target.id),
        "version": target.version,
        "is_active": True,
        "accuracy_score": target.accuracy_score,
    }


def optimize(db: Session, api_definition_id: uuid.UUID) -> dict[str, Any]:
    """
    Run iterative prompt optimization.

    Returns a summary of the optimization process.
    """
    # Validate API definition exists
    api_def = db.get(ApiDefinition, api_definition_id)
    if not api_def:
        raise NotFoundError(f"ApiDefinition {api_definition_id} not found")

    # Get sample document ID from config
    config = api_def.config or {}
    sample_doc_id_str = config.get("sample_document_id")
    if not sample_doc_id_str:
        raise ValidationError("No sample document linked to this API definition")
    try:
        sample_doc_id = uuid.UUID(sample_doc_id_str) if isinstance(sample_doc_id_str, str) else sample_doc_id_str
    except (ValueError, AttributeError):
        raise ValidationError("Invalid sample_document_id in API config")

    # Collect correction data
    corrections = (
        db.query(Annotation)
        .filter(
            Annotation.document_id == sample_doc_id,
            Annotation.is_corrected == True,  # noqa: E712
        )
        .all()
    )

    if not corrections:
        raise ValidationError("No user corrections found — nothing to optimize from")

    # Build correction examples for the meta-prompt
    correction_examples = []
    for c in corrections:
        example = {
            "field_name": c.field_name,
            "original_value": c.original_value,
            "corrected_value": c.field_value,
        }
        if c.original_bbox:
            example["location"] = c.original_bbox
        correction_examples.append(example)

    # Get current base prompt
    base_prompt = get_active_prompt(db, api_definition_id)
    if not base_prompt:
        # Generate from schema
        if api_def.response_schema:
            base_prompt = (
                f"Extract structured data from this document according to the following JSON Schema. "
                f"Return ONLY valid JSON that conforms to the schema.\n\n"
                f"Schema:\n{json.dumps(api_def.response_schema, ensure_ascii=False)}"
            )
        else:
            base_prompt = "Extract all structured data from this document and return it as valid JSON."

    # Get current max version number
    max_version = (
        db.query(PromptVersion.version)
        .filter(PromptVersion.api_definition_id == api_definition_id)
        .order_by(desc(PromptVersion.version))
        .first()
    )
    current_version = max_version[0] if max_version else 0

    # Get parent version ID
    active_version = (
        db.query(PromptVersion)
        .filter(
            PromptVersion.api_definition_id == api_definition_id,
            PromptVersion.is_active == True,  # noqa: E712
        )
        .first()
    )
    parent_id = active_version.id if active_version else None

    # Run optimization rounds
    rounds_log: list[dict] = []
    best_prompt = base_prompt
    best_accuracy = 0.0

    for round_num in range(1, MAX_ROUNDS + 1):
        logger.info("Optimization round %d/%d for api_def %s", round_num, MAX_ROUNDS, api_definition_id)

        # Build meta-prompt for LLM
        meta_prompt = _build_meta_prompt(best_prompt, correction_examples, round_num)

        # Call LLM to generate improved prompt
        try:
            new_prompt = _call_llm_for_prompt(api_def, meta_prompt)
        except Exception as exc:
            logger.warning("LLM call failed in round %d: %s", round_num, exc)
            rounds_log.append({"round": round_num, "status": "llm_error", "error": str(exc)})
            break

        if not new_prompt or new_prompt == best_prompt:
            logger.info("No improvement in round %d, stopping", round_num)
            rounds_log.append({"round": round_num, "status": "no_change"})
            break

        # Evaluate new prompt against corrections
        accuracy = _evaluate_prompt(db, api_def, sample_doc_id, new_prompt, corrections)

        rounds_log.append({
            "round": round_num,
            "status": "evaluated",
            "accuracy": round(accuracy, 3),
            "improved": accuracy > best_accuracy,
        })

        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_prompt = new_prompt
            logger.info("Round %d improved accuracy to %.1f%%", round_num, accuracy * 100)

        if best_accuracy >= TARGET_ACCURACY:
            logger.info("Target accuracy reached (%.1f%%), stopping", best_accuracy * 100)
            break

    # Save best prompt as new version (if we found something)
    if best_prompt != base_prompt or current_version == 0:
        # Deactivate old versions
        db.query(PromptVersion).filter(
            PromptVersion.api_definition_id == api_definition_id,
            PromptVersion.is_active == True,  # noqa: E712
        ).update({"is_active": False})

        new_version = PromptVersion(
            api_definition_id=api_definition_id,
            version=current_version + 1,
            prompt_text=best_prompt,
            accuracy_score=best_accuracy,
            is_active=True,
            parent_version_id=parent_id,
            optimization_metadata={
                "rounds": rounds_log,
                "corrections_count": len(corrections),
                "base_prompt_preview": base_prompt[:200],
            },
        )
        db.add(new_version)

        # Update API definition's prompt_version_id
        api_def.prompt_version_id = new_version.id
        db.commit()
        db.refresh(new_version)

        return {
            "status": "completed",
            "version": new_version.version,
            "accuracy_score": round(best_accuracy, 3),
            "rounds_completed": len(rounds_log),
            "corrections_used": len(corrections),
            "prompt_version_id": str(new_version.id),
        }

    return {
        "status": "no_improvement",
        "rounds_completed": len(rounds_log),
        "corrections_used": len(corrections),
        "message": "Optimization did not improve the prompt",
    }


def _build_meta_prompt(
    current_prompt: str, corrections: list[dict], round_num: int
) -> str:
    """Build the meta-prompt that asks the LLM to improve the extraction prompt."""
    corrections_text = json.dumps(corrections, ensure_ascii=False, indent=2)

    return f"""You are a prompt engineering expert. Your task is to improve a document extraction prompt.

## Current Prompt (Round {round_num})
{current_prompt}

## User Corrections
The following fields were extracted incorrectly by the current prompt. The user manually corrected them:

{corrections_text}

## Instructions
1. Analyze WHY the current prompt produced incorrect extractions for these fields
2. Improve the prompt to avoid these specific types of errors
3. Keep the prompt focused on document extraction
4. Maintain the JSON Schema output format requirement
5. Add specific guidance for the problematic fields
6. Return ONLY the improved prompt text, nothing else

## Improved Prompt:"""


def _call_llm_for_prompt(api_def: ApiDefinition, meta_prompt: str) -> str:
    """Call the configured LLM to generate an improved prompt."""
    from app.processors.factory import ProcessorFactory

    processor = ProcessorFactory.create(
        api_def.processor_type,
        model_name=api_def.model_name,
    )

    # Use a text file approach: write the meta-prompt to a temp file
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(meta_prompt)
        temp_path = f.name

    try:
        result = processor.process_document(
            temp_path,
            "You are a prompt engineering expert. Return ONLY the improved extraction prompt.",
            runtime_config={"response_mime_type": "text/plain"},
        )
    finally:
        os.unlink(temp_path)

    # Clean up the result — strip markdown fences if present
    result = result.strip()
    if result.startswith("```"):
        lines = result.split("\n")
        lines = lines[1:]  # Remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        result = "\n".join(lines).strip()

    return result


def _evaluate_prompt(
    db: Session,
    api_def: ApiDefinition,
    sample_doc_id: str,
    prompt: str,
    corrections: list[Annotation],
) -> float:
    """
    Evaluate a prompt by running extraction on the sample document
    and comparing against user corrections.

    Returns accuracy as 0.0-1.0.
    """
    # Get the sample document's storage path
    doc = db.get(Document, sample_doc_id)
    if not doc or not doc.storage_path:
        # Can't evaluate without the document file — return estimated accuracy
        logger.warning("Sample document %s not available for evaluation", sample_doc_id)
        return 0.5  # Unknown accuracy

    import json
    from app.processors.factory import ProcessorFactory

    try:
        processor = ProcessorFactory.create(
            api_def.processor_type,
            model_name=api_def.model_name,
        )

        raw_text = processor.process_document(doc.storage_path, prompt)
        extracted = json.loads(raw_text)
    except Exception as exc:
        logger.warning("Evaluation extraction failed: %s", exc)
        return 0.0

    # Compare extracted values against corrections
    if not isinstance(extracted, dict):
        return 0.0

    matches = 0
    total = len(corrections)

    for correction in corrections:
        field_name = correction.field_name
        expected_value = correction.field_value

        # Look up the field in extracted data (flat or nested)
        actual_value = _find_field_value(extracted, field_name)

        if actual_value is not None and _values_match(str(actual_value), str(expected_value)):
            matches += 1

    return matches / total if total > 0 else 0.0


def _find_field_value(data: dict, field_name: str) -> Any:
    """Recursively search for a field name in nested dict."""
    if field_name in data:
        return data[field_name]

    for value in data.values():
        if isinstance(value, dict):
            result = _find_field_value(value, field_name)
            if result is not None:
                return result
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _find_field_value(item, field_name)
                    if result is not None:
                        return result
    return None


def _values_match(actual: str, expected: str) -> bool:
    """Fuzzy comparison of extracted vs expected values."""
    # Exact match
    if actual.strip() == expected.strip():
        return True
    # Numeric match (handle formatting differences)
    try:
        a = float(actual.replace(",", "").strip())
        b = float(expected.replace(",", "").strip())
        return abs(a - b) < 0.01
    except (ValueError, TypeError):
        pass
    # Case-insensitive match
    if actual.strip().lower() == expected.strip().lower():
        return True
    return False
