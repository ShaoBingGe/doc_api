"""
Lightweight LLM text-completion helper.

The existing DocumentProcessor interface always expects a file_path, so for
text-only calls (e.g. asking an LLM to optimize a prompt) we write the prompt
to a temp file and call process_document. This is the same trick the legacy
optimizer used (app/optimizers/service.py:_call_llm_for_prompt).

Returns parsed JSON when `as_json=True`; raises ValueError if JSON parsing
fails after one cleanup pass.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def llm_text_completion(
    *,
    processor_spec: str,
    model_name: str | None,
    system_instruction: str,
    user_prompt: str,
    as_json: bool = True,
) -> Any:
    """
    Run a text-only LLM completion via the configured DocumentProcessor.

    Args:
        processor_spec: e.g. "gemini" or "openai" or "mock"
        model_name:     specific model (passed to ProcessorFactory.create)
        system_instruction: system prompt
        user_prompt:    user message (written to temp file as document input)
        as_json:        parse the response as JSON

    Returns:
        Parsed dict/list if as_json, else raw string.
    """
    from app.processors.factory import ProcessorFactory

    processor = ProcessorFactory.create(processor_spec, model_name=model_name)

    tmp_path: str | None = None
    try:
        # Write the user_prompt to a temp .txt file (acts as the "document")
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(user_prompt)

        runtime = {"response_mime_type": "application/json"} if as_json else \
                  {"response_mime_type": "text/plain"}
        raw = processor.process_document(tmp_path, system_instruction, runtime)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if not as_json:
        return raw

    return _parse_json_lenient(raw)


def _parse_json_lenient(raw: str) -> Any:
    """Try plain json.loads, then strip code fences and retry."""
    if raw is None:
        raise ValueError("LLM returned None")
    text = raw.strip()
    if not text:
        raise ValueError("LLM returned empty string")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip ```json ... ``` or ``` ... ```
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Last resort: find first { ... } balanced block
    snippet = _extract_balanced_braces(text)
    if snippet:
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"LLM output not valid JSON: {text[:200]!r}")


def _extract_balanced_braces(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


# ── Module enrichment (called during init_version with use_llm_for_modules) ──

def enrich_module_with_llm(module_spec: dict, api_def: Any) -> None:
    """
    Mutate `module_spec` in place: replace the heuristic description /
    suggestions / prompt with LLM-generated versions.

    Failure is non-fatal — the caller falls back to the heuristic defaults.
    """
    sys_inst = (
        "You are designing one module of a modular OCR extraction prompt. "
        "Return ONLY a single JSON object with keys "
        '"description" (string), "ocr_suggestions" (object with keys '
        '"semantics", "position", "most_common_feature", "extra_features" '
        '(list of strings)), and "ocr_prompt" (string in the same language '
        "as the document type)."
    )
    user_prompt = (
        f"Document type: {api_def.description or api_def.name}\n"
        f"Module display name: {module_spec['display_name']}\n"
        f"JSON path this module owns: {module_spec['json_path']}\n"
        f"Schema fragment:\n{json.dumps(module_spec['schema_fragment'], ensure_ascii=False, indent=2)}\n\n"
        "Design the module."
    )

    from app.processors.factory import ProcessorFactory
    _proc, _model = ProcessorFactory.resolve_spec(
        api_def.processor_type, api_def.model_name
    )
    result = llm_text_completion(
        processor_spec=_proc,
        model_name=_model,
        system_instruction=sys_inst,
        user_prompt=user_prompt,
        as_json=True,
    )

    if not isinstance(result, dict):
        return
    if "description" in result and isinstance(result["description"], str):
        module_spec["description"] = result["description"]
    if "ocr_prompt" in result and isinstance(result["ocr_prompt"], str):
        module_spec["ocr_prompt"] = result["ocr_prompt"]
    if "ocr_suggestions" in result and isinstance(result["ocr_suggestions"], dict):
        # Merge into default structure to preserve required keys
        merged = dict(module_spec["ocr_suggestions"])
        merged.update(result["ocr_suggestions"])
        module_spec["ocr_suggestions"] = merged
