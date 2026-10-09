# Flow concepts (reference)

Methodology reference for the `flow-engineering` skill. Facts only; no
commands to execute.

## Objects

```
Flow Spec -> Run -> Step -> ToolExecution -> Artifact -> Metric -> QA Finding
```

- **Flow Spec**: declarative YAML (`aemp.flow/v1`): inputs (files/params),
  steps with tool + operation + inputs + declared outputs. Dependencies
  come from artifact references (`${steps.X.outputs.Y}`), never from list
  order.
- **Run**: one tracked execution under `runs/<run_id>/`; it is the source
  of truth, indexed (not owned) by SQLite.
- **Step**: one tool operation inside the flow; per-step summaries live in
  `provenance.steps`.
- **ToolExecution**: argv, exit code, duration, bounded stdout/stderr.
- **Artifact**: registered file with role, bytes and SHA-256. Claimed
  outputs must exist; missing metrics are absent, never 0.
- **Metric**: parser-derived fact (`<domain>.<metric>_<unit>`).
- **QA Finding**: rule verdict (pass/fail/unknown) with actual/expected.

## Failure semantics (dual-state)

- `execution.status` answers "did the tool run?" - `success` / `failed` /
  `unknown`.
- `engineering.status` answers "did the QA verdict hold?" - independent.
- A failed run is a valid outcome, not an API error; an environment failure
  fails the run while `execution.status` stays `unknown`.

## Evidence discipline

Every number must trace: finding -> rule -> metric -> artifact(sha256) ->
run -> tool version. If the trace breaks, the correct answer is "unknown".
