# Ground Truth Methodology — TCP Survivor Reducer (IMP-25)

## Purpose

This document defines how ground truth labels are produced for the survivor reducer accuracy measurement. The reducer runs in shadow mode and produces a `shortlisted_tools` list per request. Ground truth tells us what the correct tool set would have been, enabling precision and recall computation.

## Label Schema

Each label is a JSON object conforming to `ground_truth_v1.json` in this directory. The primary key is `prompt_hash` (16-char SHA-256 prefix from `decisions.jsonl`).

## Annotation Methods

### 1. `oracle` (preferred for automation, highest confidence)

Uses `first_tool_name` from `decisions.jsonl` as ground truth.

**Rationale:** The model chose this tool from the full unrestricted tool list (shadow mode never prunes). When the model's choice is unambiguous and the request is unambiguous, this is the closest available signal to "what tool was actually needed."

**When to use:** When `first_tool_name` is non-null and the prompt is clearly single-tool (not a compound task).

**Confidence assignment:**
- `1.0` — single tool used, prompt is clearly single-task
- `0.8` — first tool used, but prompt might have been multi-step
- `0.6` — first tool used, but task was complex or ambiguous

**`correct_tool_set` construction:** `[first_tool_name]` when confidence is 1.0 or 0.8. For multi-step tasks, may include additional tools if the prompt clearly requires them.

**Exclusion criteria:** Exclude rows where `first_tool_name` is null (stream aborted, upstream error) or `tap_skipped` is true.

### 2. `llm_assisted` (for compound tasks)

An LLM (e.g. claude-sonnet-4-6) receives the full `prompt_text` and the list of `survivor_names_sorted` and returns a proposed `correct_tool_set`. A human reviews the proposal.

**When to use:** When the prompt clearly requires multiple tools (e.g. "read the file and then run the tests"), making a single `oracle` tool insufficient.

**Confidence assignment:** Human reviewer sets confidence. Default 0.75 if accepted without modification, lower if modified.

### 3. `human` (for ambiguous cases)

Fully manual review of the `prompt_text` against the available tool surface.

**When to use:** Ambiguous prompts, compound tasks, or any row where the annotator cannot confidently apply oracle or llm_assisted.

## Precision and Recall Definitions

Given a row's `shortlisted_tools` set S and label's `correct_tool_set` set C:

```
precision = |S ∩ C| / |S|    (of what the reducer kept, how much was correct)
recall    = |S ∩ C| / |C|    (of what was needed, how much did the reducer keep)
```

**Abstained rows:** When `reducer_abstained = true`, treat as S = full `survivor_names_sorted`. The reducer made no selection; every tool was "passed through" in shadow mode.

**Exclusion:** Rows with `confidence < 0.7` are excluded from aggregation.

## Minimum Dataset Requirements (IMP-25)

- ≥ 200 labeled rows after `DECISION_LOG_SCHEMA = 3` rows are available
- At least 50% oracle-method rows (highest confidence, cheapest to produce)
- At least 20 llm_assisted or human rows for compound-task coverage
- All rows must have `prompt_text` present (i.e. `decision_log_schema >= 3`)

## Promotion Gate

The survivor reducer may be promoted from shadow to live mode only when:

1. **Precision ≥ 0.90** across all non-excluded rows
2. **Recall ≥ 0.85** across all non-excluded rows
3. ≥ 200 labeled rows meet the exclusion criteria above
4. Results are reported with methodology stated (this document + schema version)

If precision < 0.90 or recall < 0.85, do not promote. Return a "hold" verdict with the measured numbers and a diagnosis of where the reducer is over- or under-selecting.
