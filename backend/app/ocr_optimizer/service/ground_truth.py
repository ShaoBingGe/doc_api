"""
Construct ground-truth JSON from the Annotation table for a given document.

Source-of-truth rule (design.md §15 constraint 5):
  GT = all Annotations where (is_corrected == True) OR (source == 'manual')

Annotation.field_name uses the flat-path syntax produced by
document_service._flatten_hierarchical:
    "invoice_number"
    "seller.name"
    "line_items[0].description"

This module reassembles those flat keys into a nested dict.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.annotation import Annotation, AnnotationSource


_BRACKET_RE = re.compile(r"\[(\d+)\]")


def build(db: Session, document_id: uuid.UUID) -> dict:
    """
    Build a hierarchical GT JSON dict for one document from its Annotations.

    Falls back to an empty dict if there are no annotations qualifying as GT.
    """
    qs = (
        db.query(Annotation)
        .filter(Annotation.document_id == document_id)
        .filter(
            or_(
                Annotation.is_corrected.is_(True),
                Annotation.source == AnnotationSource.manual.value,
            )
        )
        .all()
    )

    root: dict = {}
    for ann in qs:
        path = ann.field_name or ""
        if not path:
            continue
        value = _coerce_value(ann.field_value, ann.field_type)
        _insert(root, path, value)
    return root


def _coerce_value(raw: str | None, field_type: str | None) -> Any:
    """Coerce a string field_value back into its declared type."""
    if raw is None:
        return None
    if field_type == "number":
        try:
            if "." in raw:
                return float(raw)
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if field_type == "boolean":
        return raw.strip().lower() in ("true", "1", "yes", "y")
    # string / date / array fall through as string
    return raw


def _insert(root: dict, path: str, value: Any) -> None:
    """
    Insert `value` into `root` along the flat path. Auto-grows lists.

    Path syntax matches _flatten_hierarchical output: dotted keys with
    optional bracket indices, e.g. "items[0].description".
    """
    tokens = _tokenize(path)
    if not tokens:
        return

    cursor: Any = root
    for i, (kind, key) in enumerate(tokens):
        is_last = i == len(tokens) - 1

        if kind == "key":
            if is_last:
                if isinstance(cursor, dict):
                    cursor[key] = value
                return
            next_kind = tokens[i + 1][0]
            if isinstance(cursor, dict):
                if key not in cursor or not _is_container(cursor[key]):
                    cursor[key] = [] if next_kind == "index" else {}
                cursor = cursor[key]
            else:
                return  # type mismatch, drop

        else:  # kind == "index"
            idx = key  # int
            if not isinstance(cursor, list):
                return
            # grow list as needed
            while len(cursor) <= idx:
                cursor.append({})
            if is_last:
                cursor[idx] = value
                return
            next_kind = tokens[i + 1][0]
            if not _is_container(cursor[idx]):
                cursor[idx] = [] if next_kind == "index" else {}
            cursor = cursor[idx]


def _tokenize(path: str) -> list[tuple[str, Any]]:
    """
    Tokenize "items[0].name" → [("key","items"), ("index",0), ("key","name")]
    """
    tokens: list[tuple[str, Any]] = []
    for segment in path.split("."):
        if not segment:
            continue
        bracket_iter = list(_BRACKET_RE.finditer(segment))
        if not bracket_iter:
            tokens.append(("key", segment))
            continue
        key_part = segment[: bracket_iter[0].start()]
        if key_part:
            tokens.append(("key", key_part))
        for m in bracket_iter:
            tokens.append(("index", int(m.group(1))))
    return tokens


def _is_container(v: Any) -> bool:
    return isinstance(v, (dict, list))


def has_ground_truth(db: Session, document_id: uuid.UUID) -> bool:
    """Quick check: does this document have at least one GT annotation?"""
    return (
        db.query(Annotation.id)
        .filter(Annotation.document_id == document_id)
        .filter(
            or_(
                Annotation.is_corrected.is_(True),
                Annotation.source == AnnotationSource.manual.value,
            )
        )
        .first()
        is not None
    )
