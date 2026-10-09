---
name: timing-analysis
description: "Interpret OpenSTA timing results: WNS, TNS, paths and violations."
version: "1.0"
tags:
  - sta
  - timing
  - wns
  - tns
  - slack
  - violation
  - 时序
---

# Timing Analysis

## When to use

Use this skill when the user:

- asks about timing, STA, WNS/TNS or slack
- wants an existing run's timing results interpreted
- asks whether timing passes or why it fails
- needs the next action after a timing violation

## Inputs

- `run_id` of a run that already executed the opensta timing step
- the run's recorded metrics (from `get_run`)
- the artifact list (from `get_artifacts`) to locate the timing report

## Procedure

1. Call `get_run` and read the recorded timing metrics:
   - `sta.wns_ns` - worst negative slack (setup), in ns
   - `sta.tns_ns` - total negative slack, in ns
   - `sta.path_count` - number of reported timing paths
   - `sta.violation_count` - number of violated paths
2. Call `get_artifacts` to confirm the timing report exists and to quote
   its SHA-256 if the user needs evidence.
3. When a QA rule pack is available, call `run_qa` to obtain the Platform's
   deterministic verdict (PASS / FAIL / UNKNOWN). The QA engine owns the
   judgment; this skill only explains the evidence.
4. If a metric is absent from `get_run`, report it as unavailable - never
   assume 0 and never invent a value.

## Tool guidance

Preferred tools:

- `get_run`
- `get_artifacts`
- `run_qa`

Do not execute shell commands; do not run OpenSTA directly.

## Interpretation

- `sta.wns_ns >= 0`: no setup violation recorded in the report.
- `sta.wns_ns < 0`: setup timing violation exists.
- `sta.tns_ns < 0`: aggregate negative slack exists.
- `sta.violation_count > 0`: at least one path is marked VIOLATED.

These are facts from the report; the PASS/FAIL verdict comes from the
Platform QA engine with an explicit rule pack, never from this skill.

## Limitations

- Units: the `_ns` suffix is grounded in the demo library's `time_unit`
  (`1ns`); other libraries may differ and the Platform records what it
  parsed.
- No arc-level analysis (per-cell delay tables) is implemented yet.
- Do not extrapolate missing metrics or fabricate per-path detail.
