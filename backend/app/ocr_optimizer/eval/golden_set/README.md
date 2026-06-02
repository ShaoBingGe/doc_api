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

- ✅ `MY/golden_prompt.md` — 17-field gold-standard MY prompt (from the
  user-provided template, RTF-decoded 2026-06-02).
- ⛔ `MY/docs/` + `MY/ground_truth/` — **empty**. This is the gating gap for
  Phase 4.

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
