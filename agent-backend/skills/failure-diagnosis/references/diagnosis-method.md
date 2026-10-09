# Diagnosis method (reference)

Methodology reference for the `failure-diagnosis` skill. Evidence-first
reasoning; no RAG, no historical case database (those are later
milestones).

## Evidence checklist

For one `run_id`, read in this order:

1. `lifecycle_status` - where the run stopped.
2. `execution_status` + exit code - did the tool process run?
3. `diagnostics[]` - environment / timeout / missing_artifact codes.
4. `metrics` - parser facts (what the tools reported).
5. QA findings (`run_qa`) - rule_id, actual, expected, severity.

## Failure classes

| Observation | Class | Next action |
|---|---|---|
| execution unknown + `environment` diagnostic | Platform environment (no Docker/daemon) | Fix the execution environment; the design was never evaluated |
| execution failed + exit code != 0 | Tool-level failure | Inspect the archived stdout/stderr artifacts; quote the exit code |
| execution success + engineering fail | Design/QA failure | Read the QA findings; identify which rule/metric failed |
| missing metric | Evidence gap | Do not infer; rerun or inspect the report artifact |

## Hypothesis discipline

- Rank hypotheses by the strength of recorded evidence, not plausibility.
- One concrete next action per hypothesis (rerun, inspect artifact, adjust
  rule pack, fix environment).
- Never state a cause as fact when only correlation is available; mark
  hypotheses as hypotheses.
- The Platform says WHICH rule failed; diagnosis says WHY - and any numbers
  quoted must come from the run archive.
