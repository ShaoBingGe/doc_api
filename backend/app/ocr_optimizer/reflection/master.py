"""
Master router for the reflection layer.

Given a single field diff, returns the ordered list of Skills that apply.
Multiple skills can match a single diff — e.g. a value+format change.
"""

from __future__ import annotations

import logging

from .skills_loader import Skill, load_skills

logger = logging.getLogger(__name__)


def route(diff: dict) -> list[Skill]:
    """Return all skills whose `match` predicate is satisfied by `diff`.

    Empty list = diff didn't fall into any known reflection category. Caller
    decides whether that's a no-op or a default-pass.
    """
    all_skills = load_skills()
    matches: list[Skill] = []
    for skill in all_skills:
        try:
            if skill.matches(diff):
                matches.append(skill)
        except Exception as exc:
            logger.exception("Skill %s match() raised on diff=%s: %s",
                             skill.key, diff, exc)
    if not matches:
        logger.info("No reflection skill matched diff kind=%s field=%s",
                    diff.get("kind"), diff.get("module_key") or diff.get("corrected_name"))
    return matches
