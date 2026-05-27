"""
JSONPath slicer — given an OCR output dict and a `json_path` string from a
module, return the slice of data this module is responsible for.

Supported path syntax (subset of JSONPath):
  $                 -> root
  $.foo             -> root.foo
  $.foo.bar         -> root.foo.bar
  $.items           -> root.items (the whole array/object)
  $.items[*]        -> root.items (returns the list)
  $.items[*].name   -> [item.name for item in root.items]
  $.items[0]        -> root.items[0]
  foo.bar           -> same as $.foo.bar (leading $ optional)

Missing intermediate keys return None (not raise).
"""

from __future__ import annotations

import re
from typing import Any


_BRACKET_RE = re.compile(r"\[(\*|\d+)\]")


def parse_path(path: str) -> list:
    """
    Parse a JSONPath-like string into a list of step tokens.

    Each step is either:
      - a string (dict key)
      - an int (list index)
      - the special token "*" (wildcard over list)
    """
    if not path:
        return []
    p = path.strip()
    if p.startswith("$"):
        p = p[1:]
    if p.startswith("."):
        p = p[1:]
    if not p:
        return []

    steps: list = []
    for segment in p.split("."):
        if not segment:
            continue
        # split off bracket parts: e.g. "items[*]" -> key "items" + brackets
        bracket_iter = list(_BRACKET_RE.finditer(segment))
        if not bracket_iter:
            steps.append(segment)
            continue
        # key part (before first bracket)
        first = bracket_iter[0]
        key_part = segment[: first.start()]
        if key_part:
            steps.append(key_part)
        # each bracket
        for m in bracket_iter:
            token = m.group(1)
            if token == "*":
                steps.append("*")
            else:
                steps.append(int(token))
    return steps


def extract(data: Any, path: str) -> Any:
    """
    Extract the slice of `data` indicated by `path`.

    Wildcards on lists return a list. If any step misses, returns None.
    """
    if data is None:
        return None
    steps = parse_path(path)
    if not steps:
        return data
    return _walk(data, steps)


def _walk(node: Any, steps: list) -> Any:
    if not steps:
        return node
    if node is None:
        return None
    head, rest = steps[0], steps[1:]

    if head == "*":
        if not isinstance(node, list):
            return None
        return [_walk(item, rest) for item in node]

    if isinstance(head, int):
        if not isinstance(node, list) or head >= len(node) or head < -len(node):
            return None
        return _walk(node[head], rest)

    # string key
    if isinstance(node, dict):
        if head not in node:
            return None
        return _walk(node[head], rest)
    return None


def collect_leaf_paths(data: Any, prefix: str = "$") -> list[str]:
    """
    Walk a JSON tree and emit JSONPath strings for every leaf value.

    Used by meta_optimizer to find "unclaimed" GT fields (paths in GT that no
    module's json_path covers).
    """
    paths: list[str] = []
    _collect(data, prefix, paths)
    return paths


def _collect(node: Any, prefix: str, out: list[str]) -> None:
    if isinstance(node, dict):
        if not node:
            out.append(prefix)
            return
        for k, v in node.items():
            _collect(v, f"{prefix}.{k}", out)
    elif isinstance(node, list):
        if not node:
            out.append(prefix)
            return
        # collapse array items to a single "[*]" path summary
        # but also descend into structure of first element if dict
        if isinstance(node[0], dict):
            _collect(node[0], f"{prefix}[*]", out)
        else:
            out.append(f"{prefix}[*]")
    else:
        out.append(prefix)


def path_covers(module_path: str, leaf_path: str) -> bool:
    """
    Return True if `leaf_path` is a descendant of `module_path` (or equal).

    Treats `[*]` and `[N]` as equivalent for coverage purposes.
    """
    mp = _normalize_for_compare(module_path)
    lp = _normalize_for_compare(leaf_path)
    return lp == mp or lp.startswith(mp + ".") or lp.startswith(mp + "[")


def _normalize_for_compare(p: str) -> str:
    p = p.strip()
    if not p.startswith("$"):
        p = "$" + ("." + p if p and not p.startswith(".") else p)
    # collapse [N] -> [*] for stable comparison
    return re.sub(r"\[\d+\]", "[*]", p)
