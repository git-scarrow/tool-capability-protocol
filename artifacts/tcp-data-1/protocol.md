# TCP-DATA-1 Labeling Protocol

This package is the execution artifact for TCP-DATA-1. It replaces the synthetic audit path for validation purposes.

## Goal

Produce a real hand-labeled audit set that can unblock TCP-VAL-1.

## Label each row with

1. `ground_truth_flags`
2. `ground_truth_formats`
3. `notes`

Fill these concrete fields in the JSONL rows:

- `rater1_flags`, `rater1_formats`, `rater1_notes`
- `rater2_flags`, `rater2_formats`, `rater2_notes` for calibration or second-pass review
- `adjudication_required`
- `final_flags`, `final_formats`, `final_notes`
- `label_status` as `unlabeled`, `calibrated`, `needs_adjudication`, or `final`

## Capability flag semantics

- `SUPPORTS_FILES = 1`
- `SUPPORTS_NETWORK = 4`
- `AUTH_REQUIRED = 8192`

Use bitwise OR when multiple capabilities are required.

## Output format semantics

- Default: `text`
- Add `json` only when the prompt explicitly asks for structured JSON
- Add `binary` only when the prompt explicitly asks for file-like or binary output

## Calibration slice

- Double-label the first 10 rows in `calibration_slice.jsonl`
- Compare labels and compute Cohen's kappa
- Target: `kappa >= 0.80`
- Maximum attempts before halt: 2

## Acceptance gates

- Hand-labeled candidate set contains at least 50 turns
- At least 20 turns are suitable for coverage-delta audit
- Full set is suitable for precision/recall scoring
- Calibration completed and agreement threshold met
- Adjudication log completed for all disagreements

## Blocking artifact for TCP-VAL-1

The blocker clears when `candidate_turns.jsonl` has final hand labels populated and the calibration/adjudication artifacts are complete.

## Notes

The synthetic audit generator is development-only smoke coverage. Do not use it as validation evidence.
