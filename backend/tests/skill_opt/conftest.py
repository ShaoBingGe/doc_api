"""Shared fixtures for the skill-optimization (ReflACT) test suite.

See docs/skill-optimization-test-cases.md for the full catalog. These are
DESIGN-STAGE stubs: they pin the interface each test relies on, and skip
cleanly until the corresponding implementation phase lands. No fixture here
calls the real VLM — L0/L1 are token-free by construction (the whole point
of the test philosophy in §0 of the catalog).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Japan-inv corpus lives OUTSIDE the repo (291MB, gitignored). Tests that need
# it must skip gracefully when it's absent (CI / fresh clone).
_REPO_ROOT = Path(__file__).resolve().parents[3]
JAPAN_INV = _REPO_ROOT / "Japan-inv"


@pytest.fixture(scope="session")
def japan_inv_root() -> Path:
    """Path to the Japan-inv corpus; skip the test if not present locally."""
    if not (JAPAN_INV / "train" / "labels").is_dir():
        pytest.skip("Japan-inv corpus not present (gitignored local data)")
    return JAPAN_INV


@pytest.fixture
def japan_inv(japan_inv_root):
    """Returns load(split, k=None, seed=42) -> [(pdf_path, gt_entity), ...].

    Pending P0' (bench_japan_inv). Stub raises so the dependent test skips.
    """
    pytest.skip("P0' pending: bench_japan_inv loader not implemented")


@pytest.fixture
def mock_processor():
    """Fake processor: script[sample_id] -> structured_data, ZERO VLM calls.

    Counts invocations so L4 cost tests can assert call counts. Pending P0'.
    """
    pytest.skip("P0' pending: mock processor harness not implemented")


@pytest.fixture
def synthetic_rollouts():
    """Build OcrModuleIteration.per_sample_results with controllable
    hard/soft/per-field accuracy — the core of every token-free L1 test.

    Pending P0' types (RolloutScore). Stub skips.
    """
    pytest.skip("P0' pending: synthetic rollout factory not implemented")


@pytest.fixture
def golden_baseline():
    """Read ckpt/bench/baseline.json (P0' snapshot) for L2 comparisons."""
    p = _REPO_ROOT / "backend" / "ckpt" / "bench" / "baseline.json"
    if not p.is_file():
        pytest.skip("Baseline snapshot not present (run P0' bench first)")
    import json
    return json.loads(p.read_text())


# Marks tests that touch the real VLM — excluded from default CI, run nightly.
live_ocr = pytest.mark.live_ocr
