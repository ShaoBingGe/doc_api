"""L0 — Japan-inv benchmark harness self-tests (gates P0').

Catalog: docs/skill-optimization-test-cases.md §2 L0. Deterministic, token-free.
H03/H04/H06 use synthetic in-memory data (CI-runnable, no corpus); H01/H02/H05
validate the real Japan-inv corpus (skip when the local corpus is absent).
"""
from pathlib import Path


# ── H01 — splits load and pair ───────────────────────────────────────────────

def test_SKT_H01_splits_load_and_pair(japan_inv_root, bench):
    """train/val/test/init counts 182/73/108/8; every loaded pair has an
    existing PDF + parsed GT entity."""
    expected = {"train": 182, "val": 73, "test": 108, "init": 8}
    for split, n in expected.items():
        pairs = bench.load(split)
        assert len(pairs) == n, f"{split}: {len(pairs)} != {n}"
        for pdf_path, gt in pairs:
            assert pdf_path.exists()
            assert isinstance(gt, dict)


# ── H02 — field alignment (no mapping layer) ─────────────────────────────────

def test_SKT_H02_field_alignment(japan_inv_root, bench):
    """Every platform-canonical scalar field appears as a GT key somewhere in a
    train sample → names align 1:1 (a GT only writes present fields, so check the
    union across samples)."""
    pairs = bench.load("train", k=60, seed=42)
    seen_keys: set[str] = set()
    for _pdf, gt in pairs:
        seen_keys |= set(gt.keys())
    missing = [f for f in bench.CANON_JP_FIELDS if f not in seen_keys]
    assert not missing, f"canonical fields never seen in GT: {missing}"


# ── H03 — scorer wiring (pure, no corpus) ────────────────────────────────────

def test_SKT_H03_scorer_wiring(bench):
    """evaluator.compare reuse: identical pred==GT → hard=1.0; one wrong field →
    hard<1 and soft∈(0,1)."""
    gt = {f: "x" for f in bench.CANON_JP_FIELDS}
    perfect = bench.score_pred(dict(gt), gt)
    assert perfect["hard"] == 1.0
    assert perfect["soft"] == 1.0

    one_wrong = dict(gt)
    one_wrong[bench.CANON_JP_FIELDS[0]] = "WRONG"
    s = bench.score_pred(one_wrong, gt)
    assert s["hard"] < 1.0
    assert 0.0 < s["soft"] < 1.0
    assert s["per_field"][bench.CANON_JP_FIELDS[0]]["hard"] is False


# ── H04 — three numbers (synthetic pairs + mock, no corpus) ──────────────────

def test_SKT_H04_three_numbers(bench, mock_processor):
    """run_split aggregates hard/soft correctly and counts one OCR call per
    sample. 2 perfect + 1 fully-wrong → hard = 2/3."""
    fields = ["docType", "invoiceNumber"]
    pairs = [
        (Path("/x/a.pdf"), {"docType": "invoice", "invoiceNumber": "A1"}),
        (Path("/x/b.pdf"), {"docType": "receipt", "invoiceNumber": "B2"}),
        (Path("/x/c.pdf"), {"docType": "invoice", "invoiceNumber": "C3"}),
    ]
    script = {
        "a.pdf": {"docType": "invoice", "invoiceNumber": "A1"},   # perfect
        "b.pdf": {"docType": "receipt", "invoiceNumber": "B2"},   # perfect
        "c.pdf": {"docType": "WRONG", "invoiceNumber": "WRONG"},  # both wrong
    }
    predict = mock_processor(script)
    res = bench.run_split(predict, pairs, fields)
    assert res["n"] == 3
    assert res["ocr_calls"] == 3
    assert predict.state["calls"] == 3
    assert abs(res["hard"] - (2 / 3)) < 1e-9


# ── H05 — reproducibility (corpus) ───────────────────────────────────────────

def test_SKT_H05_reproducible(japan_inv_root, bench):
    """Same seed twice → identical sampled set (by filename) and order."""
    a = bench.load("train", k=12, seed=42)
    b = bench.load("train", k=12, seed=42)
    assert [p.name for p, _ in a] == [p.name for p, _ in b]
    # a different seed should (very likely) differ
    c = bench.load("train", k=12, seed=7)
    assert {p.name for p, _ in a} != {p.name for p, _ in c}


# ── H06 — OCR cache hit (synthetic, no corpus) ───────────────────────────────

def test_SKT_H06_ocr_cache_hit(bench, mock_processor):
    """Second score of the same (sample, skill_version) does NOT re-invoke the
    underlying predictor (hit counter rises; underlying call count flat)."""
    predict = mock_processor({"a.pdf": {"docType": "invoice"}})
    cached = bench.CachingPredictor(predict, skill_version="v1")
    pdf = Path("/x/a.pdf")

    cached(pdf)
    cached(pdf)  # same key → cache hit
    assert cached.calls == 1
    assert cached.hits == 1
    assert predict.state["calls"] == 1

    # a new skill version is a cache miss → underlying called again
    cached2 = bench.CachingPredictor(predict, skill_version="v2")
    cached2(pdf)
    assert predict.state["calls"] == 2
