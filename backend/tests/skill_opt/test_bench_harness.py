"""L0 — Japan-inv benchmark harness self-tests (gates P0').

Catalog: docs/skill-optimization-test-cases.md §2 L0. Deterministic, token-free
(mock_processor). Each test's docstring IS the case spec; bodies skip until the
P0' harness (bench_japan_inv) exists, then get implemented per the spec.
"""
import pytest

pytestmark = pytest.mark.skip(reason="P0' pending: bench_japan_inv harness")


def test_SKT_H01_splits_load_and_pair(japan_inv_root):
    """Load train/val/test/init → counts 182/73/108/8; every docs/<X>.pdf has a
    matching labels/<X>.pdf.json (and vice versa)."""


def test_SKT_H02_field_alignment(japan_inv_root):
    """Platform canonical JP fields (docType … billFromTaxIdentificationNumber)
    are all present as keys in the GT entity schema — no mapping layer needed."""


def test_SKT_H03_scorer_wiring():
    """evaluator.compare: prediction==GT → hard=1.0; one wrong field → hard=0.0
    and soft ∈ (0,1). Validates we reuse the existing zero-tolerance scorer."""


def test_SKT_H04_three_numbers(mock_processor):
    """mock_processor with known predictions → harness computes train/val/test
    field accuracy + train-test gap + OCR call count == #samples (matches hand
    calculation)."""


def test_SKT_H05_reproducible(mock_processor):
    """Same seed twice → identical sampled sets and bit-identical three numbers."""


def test_SKT_H06_ocr_cache_hit(mock_processor):
    """Second score of the same (sample, skill_version) does NOT re-invoke the
    processor (cache-hit counter rises; processor call count flat)."""
