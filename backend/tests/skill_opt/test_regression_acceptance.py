"""L2 acceptance (Japan-inv three numbers) + L3 regression guards + L4 cost.

Catalog: docs/skill-optimization-test-cases.md §2 L2/L3/L4.

- L2 (live_ocr): manual/nightly, real OCR, run at phase boundaries only.
- L3: deterministic guards mostly token-free (golden_set uses cached scores).
- L4: call-count assertions on the mock harness (token-free).
"""
import pytest

pytestmark = pytest.mark.skip(reason="P1/P2 pending")


# ── L2 acceptance — the three numbers vs baseline (real OCR, run rarely) ──────

@pytest.mark.live_ocr
def test_SKT_A01_generalization_not_worse(japan_inv, golden_baseline):
    """test(108) full, once post-rework → field accuracy ≥ baseline."""


@pytest.mark.live_ocr
def test_SKT_A02_overfitting_gap_narrows(japan_inv, golden_baseline):
    """Iterate on only 3 cherry-picked samples; (train_acc − test_acc) gap with
    the Gate < gap without it, and test_acc does not drop. The central hypothesis
    of the whole rework — proposed as a P1 HARD gate."""


@pytest.mark.live_ocr
def test_SKT_A03_noise_elbow_confirms_N(japan_inv):
    """k ∈ {3,5,8,12,16,24}, reduced test subset (50) → accuracy saturates; the
    elbow ≈ planned N (confirms N=9 is reasonable; sets the production threshold)."""


@pytest.mark.live_ocr
def test_SKT_A04_skill_reuse_cold_start(japan_inv):
    """P2: a 2nd same-country API using the global skill library cold-starts at
    higher test accuracy than with no skill."""


# ── L3 regression guards — must not break ────────────────────────────────────

@pytest.mark.live_ocr
def test_SKT_RG01_golden_set_not_worse():
    """MY/JP eval/golden_set scores ≥ pre-rework (cached where possible)."""


def test_SKT_RG02_country_lock_untouched(synthetic_rollouts):
    """After iteration, the 4 JP country-locked fields' schema_fragment + ocr_prompt
    are byte-unchanged (excluded from reflection + pinned). Token-free."""


def test_SKT_RG03_monotonic_finalize(synthetic_rollouts):
    """Final version's val score ≥ starting version (existing invariant holds)."""


def test_SKT_RG04_customer_override_still_applies(synthetic_rollouts):
    """field_constraints projection/pin is not clobbered by the skill layer."""


def test_SKT_RG05_extraction_chain_unchanged():
    """Existing document_service projection/normalization tests stay green."""


# ── L4 cost budget — token-free counters ─────────────────────────────────────

def test_SKT_B01_gate_adds_no_ocr(mock_processor):
    """Gate reuses step-1 OCR of the 12 samples; it issues ZERO extra OCR calls."""


def test_SKT_B02_early_exit_on_all_pass(mock_processor):
    """A round where every field is already 1.0 → skip optimize + OCR entirely."""


def test_SKT_B03_new_llm_stages_bounded(mock_processor):
    """reflect/clip/gate LLM calls per round ≤ budget (asserted on mock counts)."""
