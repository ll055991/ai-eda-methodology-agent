# STA metrics (reference)

Methodology reference for the `timing-analysis` skill. Derived from the
Platform's real OpenSTA parser output (`src/aemp/parsers/opensta.py`).

## Recorded metrics

| Metric | Unit | Meaning |
|---|---|---|
| `sta.wns_ns` | ns | worst negative slack (setup), from `worst slack max` |
| `sta.tns_ns` | ns | total negative slack, from `tns max` |
| `sta.path_count` | count | number of reported timing paths |
| `sta.violation_count` | count | paths with `(VIOLATED)` status |

## How the numbers are produced

1. The flow's opensta step writes its stdout; the engine archives it as
   `artifacts/timing.rpt` (role `<step>.report`).
2. The OpenSTA parser (pure function) reads that artifact and emits typed
   metrics plus per-path summaries (startpoint, endpoint, group, type,
   slack, status).
3. `run_qa` applies a YAML rule pack (e.g. `base_timing`: WNS >= 0,
   TNS >= 0) and produces findings with rule_id/actual/expected/severity.
4. `engineering.status` reflects the QA gate: any error-severity fail ->
   fail; any unknown -> unknown; else pass.

## Honest failure modes

- A report missing the WNS line leaves `sta.wns_ns` absent (with a
  `metric_unavailable` diagnostic) - never 0.
- An empty or unrecognizable report is a parse error, not a violation.
- A negative WNS with exit code 0 is still `execution.status = success`:
  the tool ran, the design has a timing issue.
