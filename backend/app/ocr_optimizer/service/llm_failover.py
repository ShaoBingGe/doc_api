"""
Multi-provider failover wrapper for LLM text completions.

Reads the `LLM_FALLBACK_CHAIN` setting (semicolon-separated `provider|model`
entries) and tries each in turn until one succeeds. If the chain is empty or
unparseable, falls back to a single attempt with the caller-provided
`processor_spec` / `model_name`.

Failure criteria (a chain step is considered failed when):
  - llm_text_completion raises any Exception (network, SSL, JSON parse, etc.)
The wrapper logs which provider was used and which were skipped.

Only intended for offline LLM calls (reflection, prompt optimization). The
production OCR endpoint MUST NOT use this — it should remain deterministic
on the API's configured processor.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.core.config import get_settings

from .llm_call import llm_text_completion

logger = logging.getLogger(__name__)


def _parse_chain(raw: str) -> list[tuple[str, str | None]]:
    """Parse `prov1|model1;prov2|model2;...` into [(prov, model), ...].

    Empty model is allowed (means "use the provider's default").
    """
    out: list[tuple[str, str | None]] = []
    for entry in (raw or "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "|" in entry:
            p, m = entry.split("|", 1)
            p = p.strip()
            m = m.strip()
            out.append((p, m or None))
        else:
            out.append((entry, None))
    return out


def get_chain(
    *,
    primary_spec: str,
    primary_model: str | None,
) -> list[tuple[str, str | None]]:
    """Resolve the effective chain.

    Always starts with (primary_spec, primary_model), then appends any
    additional entries from LLM_FALLBACK_CHAIN that aren't duplicates.

    Availability filter: entries whose provider has no credentials / deps in
    the current environment are dropped up front — a stale chain config
    (e.g. gemini-first on a server that can't reach Gemini) must not burn a
    failed call per step before reaching a working provider. If filtering
    empties the chain, fall back to [DEFAULT_PROCESSOR(resolved), mock].
    """
    from app.processors.factory import ProcessorFactory

    settings = get_settings()
    chain_cfg = _parse_chain(getattr(settings, "LLM_FALLBACK_CHAIN", "") or "")
    chain: list[tuple[str, str | None]] = []

    if primary_spec:
        chain.append((primary_spec, primary_model))

    for entry in chain_cfg:
        if entry not in chain:
            chain.append(entry)

    filtered = [
        (p, m) for (p, m) in chain
        if p == "mock" or ProcessorFactory.is_available(p)
    ]
    dropped = [p for (p, m) in chain if (p, m) not in filtered]
    if dropped:
        logger.info("LLM chain: dropped unavailable providers %s", dropped)

    if not any(p != "mock" for (p, _m) in filtered):
        resolved, _ = ProcessorFactory.resolve_spec(None)
        if resolved != "mock":
            filtered.insert(0, (resolved, None))

    if not any(p == "mock" for (p, _m) in filtered):
        filtered.append(("mock", None))

    return filtered


def llm_text_completion_failover(
    *,
    processor_spec: str,
    model_name: str | None,
    system_instruction: str,
    user_prompt: str,
    as_json: bool = True,
) -> Any:
    """Same signature as llm_text_completion, but tries the failover chain.

    Raises the LAST exception only if every link fails.
    """
    chain = get_chain(primary_spec=processor_spec, primary_model=model_name)
    if not chain:
        # Defensive: no provider configured anywhere — degrade to mock
        chain = [("mock", None)]

    last_exc: Exception | None = None
    for idx, (prov, model) in enumerate(chain):
        try:
            result = llm_text_completion(
                processor_spec=prov,
                model_name=model,
                system_instruction=system_instruction,
                user_prompt=user_prompt,
                as_json=as_json,
            )
            if idx > 0:
                logger.info(
                    "LLM failover: succeeded with chain step %d (%s|%s) after %d earlier failures",
                    idx, prov, model, idx,
                )
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "LLM failover: step %d (%s|%s) failed: %s",
                idx, prov, model, type(exc).__name__,
            )
            continue

    # Every link failed
    assert last_exc is not None
    raise last_exc
