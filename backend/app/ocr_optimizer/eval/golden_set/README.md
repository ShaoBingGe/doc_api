# Golden set — Prompt System v2 evaluation data

This directory is the **measurement substrate** for the prompt-v2 phases. The
harness (`eval/run_eval.py`, `eval/harness.py`) runs a candidate prompt over a
fixed set of documents and scores each field against ground truth, so every
prompt/skill change can be proven "no regression" before merge.

## Layout (per country)

```
golden_set/
└── <COUNTRY>/                  e.g. MY
    ├── golden_prompt.md        expert gold-standard composed_prompt (REFERENCE
    │                           baseline to benchmark generated prompts against)
    ├── docs/                   golden source documents (invoice PDFs / images)
    │   ├── inv-001.pdf
    │   └── ...
    └── ground_truth/           one JSON per doc (SAME basename), the correct
        ├── inv-001.json        extraction — human-verified
        └── ...
```

- **golden_prompt.md** — the curated, expert-authored full prompt. Used as the
  "A" baseline in `benchmark_ab` (golden vs. system-generated), and as a target
  the optimizer should approach. NOT the live template — the live MY template
  lives in `MY_invoice_prompt.yaml` + composer.
- **docs/ + ground_truth/** — what actually lets us *measure accuracy*. A prompt
  alone proves nothing; we need real documents and the correct answers.

## What's present

- ✅ `MY/golden_prompt.md` — 17-field gold-standard MY prompt (RTF-decoded).
- ✅ `MY/ground_truth/*.json` + `MY/manifest.json` — **37 MY docs**, exported
  from existing 已审视 samples via `build_golden_set.py` (deduped by file
  content hash; field counts 9–57; covers SST invoices, Credit Notes, Panasonic,
  PP Chin Hin rentals, etc.).
- 📁 `MY/docs/` — the 37 source invoice scans live here LOCALLY but are
  **gitignored** (PII + 11 MB binary + regenerable). Rebuild any time with
  `python build_golden_set.py` (invoked with `app.models` imported first to
  dodge the cold-start circular import).

### Rebuild / refresh

```
cd backend
python -c "import app.models, app.ocr_optimizer; \
  from app.ocr_optimizer.eval.build_golden_set import main; main(['--country','MY'])"
```

## ⚠️ Ground-truth root shape — and a production scoring bug it exposed

`ground_truth.build()` reassembles a **dict** root (the annotation field_names
lost the leading `[0]` when the list-rooted `structured_data` was flattened).
But module `json_path`s are **array-rooted** (`$[*].invoiceNumber`) and the OCR
output is a **list**. So `slicer.extract(dict_gt, "$[*].x")` returns `None` for
*every* field.

This is not just a golden-set concern — it's a **live bug in
`run_orchestrator._run_one_round`**: it slices the dict GT with `$[*].` paths,
so per-field accuracy is driven by root-type coincidence, not correctness
(both-None → false 1.0 → spurious early-stop; list-vs-None → false 0.0). Recorded
round accuracies (1.0 / 0.0 / 0.6667) are therefore not measuring real accuracy.

The golden set sidesteps this by storing GT **list-wrapped** (`[gt_dict]`), so it
matches the array json_paths + list OCR root and measures truth. **Fixing the
live scoring is the real Phase-4 prerequisite** (more impactful than the
reconciler) — the golden set is exactly the gate to validate that fix.

## To START Phase 4 (the gate), we need golden docs + ground truth

Phase 4 (composer renders the FieldRule skeleton, replacing the accumulated
ocr_prompt, plus the cross-round contradiction reconciler) is a HIGH-impact
change. Per skill-creator's "measure before you change", it must show no
accuracy regression on a real A/B. That requires, per country:

1. **Golden documents** — ~5–10 representative MY invoices (PDF/image) covering
   the edge cases the prompt cares about: multi-page, Credit Note, dual
   tax/TIN, dual currency, horizontal table headers.
2. **Ground truth JSON** for each — the correct extraction, **human-verified**
   (gate quality == GT quality).

### Two ways to obtain them

- **(A) Export from existing confirmed samples — fastest, nothing new needed.**
  Several ApiDefs already have "已审视" (GT-confirmed) samples (e.g. the chinkin
  template's 3 docs). A small exporter can dump each doc file + its
  `ground_truth.build()` JSON straight into `MY/docs/` + `MY/ground_truth/`.
  Just name the ApiDef(s) to seed from.
- **(B) Curate a fresh diverse set.** Provide 5–10 MY invoices covering the edge
  cases above + confirm their correct extraction.

Recommended: start with (A) to bootstrap, then top up with (B) for the missing
edge cases. Once `docs/` + `ground_truth/` exist, `run_eval.py` produces a
baseline benchmark and Phase 4 has its gate.
