---
name: failure-diagnosis
description: Diagnose failed runs and QA failures from recorded evidence.
version: "1.0"
tags:
  - failure
  - fail
  - failed
  - error
  - diagnose
  - debug
  - exception
  - 失败
  - 诊断
---

# Failure Diagnosis

## When to use

Use this skill when the user:

- says a run failed, errored, timed out or got stuck
- asks why QA returned FAIL
- wants recommended next actions after a failure

## Inputs

All evidence comes from the Platform archive of one `run_id`:

- `execution` block: status, exit code, duration, log paths
- `diagnostics`: environment, timeout, missing-artifact entries
- `metrics`: parser-derived facts (e.g. `sta.wns_ns`)
- QA findings: rule_id, actual, expected, severity, status

## Procedure

1. Call `get_run` for the `run_id` and read, in order:
   `lifecycle_status` -> `execution_status` -> `engineering_status` ->
   diagnostics -> metrics.
2. Distinguish the failure class honestly:
   - `execution_status = unknown` + an `environment` diagnostic: the tool
     never ran (Platform environment problem), not a design failure.
   - `execution_status = failed` + exit code: the tool ran and failed;
     quote the exit code.
   - `execution_status = success` + `engineering_status = fail`: the tool
     ran fine; QA rules failed (design/methodology issue).
3. Call `get_artifacts` to identify the evidence files behind the numbers.
4. Call `run_qa` (when a rule pack exists) to obtain findings; each finding
   carries rule_id / actual / expected / severity.
5. Form 2-3 plausible root-cause hypotheses ranked by evidence, and one
   concrete next action per hypothesis.
6. State clearly which facts you do NOT have.

## Tool guidance

Preferred tools:

- `get_run`
- `get_artifacts`
- `run_qa`

## Limitations

- M7.1 implements diagnosis as a **methodology**, not RAG/memory: there is
  no historical case database and no vector search yet.
- Never invent metrics or log lines. Missing evidence is reported as
  unavailable, not guessed.
- Do not confuse "tool exited 0" with "engineering passed" - the verdict
  belongs to the Platform QA engine.
