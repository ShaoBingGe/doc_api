"""
Reflection layer for the customer-driven prompt iteration flow.

When a customer edits or adds fields in the workspace (the "field correction"
flow), each diff is routed through this layer BEFORE being fed into the
3-round optimizer. Sub-agent skills produce a structured rationale about why
the original prompt likely failed and what to change.

Public entrypoints:
    reflector.reflect_on_diffs(diffs, modules) -> dict[module_key, ReflectionResult]
    master.route(diff) -> list[Skill]  (internal)
    skills.load_skills() -> list[Skill] (loads YAML configs lazily)
"""

from .reflector import reflect_on_diffs, ReflectionResult

__all__ = ["reflect_on_diffs", "ReflectionResult"]
