"""Shared fixtures for the skill-optimization (ReflACT) test suite.

See docs/skill-optimization-test-cases.md for the full catalog. No fixture here
calls the real VLM — L0/L1 are token-free by construction (test philosophy §0).
L0 harness fixtures are LIVE (P0' landed); L1/L2 stubs skip until their phase.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Repo root: backend/tests/skill_opt/conftest.py → parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
JAPAN_INV = _REPO_ROOT / "Japan-inv"


@pytest.fixture(scope="session")
def japan_inv_root() -> Path:
    """Path to the Japan-inv corpus; skip when not present (CI / fresh clone —
    it is 291MB, gitignored, local only)."""
    if not (JAPAN_INV / "train" / "labels").is_dir():
        pytest.skip("Japan-inv corpus not present (gitignored local data)")
    return JAPAN_INV


@pytest.fixture
def bench():
    """The P0' benchmark harness module (token-free core)."""
    from app.ocr_optimizer.eval import bench_japan_inv
    return bench_japan_inv


@pytest.fixture
def mock_processor():
    """Factory: make(script) -> counting predict_fn that returns
    `script[pdf.name]` (or {}) and NEVER calls the VLM. `.calls` counts hits."""
    def _make(script: dict[str, dict]):
        state = {"calls": 0}

        def predict(pdf_path):
            state["calls"] += 1
            return script.get(Path(pdf_path).name, {})

        predict.state = state  # type: ignore[attr-defined]
        return predict

    return _make


@pytest.fixture
def japan_inv(japan_inv_root):
    """Loader callable `load(split, k=None, seed=42)` — used by L2 (live)."""
    from app.ocr_optimizer.eval import bench_japan_inv
    return bench_japan_inv.load


# ── L1/L2 stubs — skip until their implementation phase ──────────────────────

@pytest.fixture
def synthetic_rollouts():
    """Factory: make(spec) -> list[RolloutScore], the core of every token-free
    L1 test. spec = {sample_id: {field: (hard, soft?, error?)}}; soft defaults
    to 1.0/0.0 from hard, error defaults to '<field>_err' when wrong."""
    from app.ocr_optimizer.skilltrain.types import FieldResult, RolloutScore

    def make(spec: dict):
        out = []
        for sid, fields in spec.items():
            frs = {}
            for f, v in fields.items():
                v = v if isinstance(v, (tuple, list)) else (v,)
                hard = bool(v[0])
                soft = float(v[1]) if len(v) > 1 else (1.0 if hard else 0.0)
                err = v[2] if len(v) > 2 else ("" if hard else f"{f}_err")
                frs[f] = FieldResult(field=f, hard=hard, soft=soft, error=err)
            out.append(RolloutScore(sample_id=sid, fields=frs))
        return out

    return make


@pytest.fixture
def golden_baseline():
    """Read ckpt/bench/baseline.json (P0' snapshot) for L2 comparisons."""
    p = _REPO_ROOT / "backend" / "ckpt" / "bench" / "baseline.json"
    if not p.is_file():
        pytest.skip("Baseline snapshot not present (run P0' bench first)")
    return json.loads(p.read_text())
