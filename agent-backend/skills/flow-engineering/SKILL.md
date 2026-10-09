---
name: flow-engineering
description: Execute and reason about EDA flow runs, steps, artifacts and failures.
version: "1.0"
tags:
  - flow
  - run
  - rtl
  - synthesis
  - synth
  - run_flow
  - 流程
---

# Flow Engineering

## When to use

Use this skill when the user:

- wants to run a flow (e.g. RTL to STA) or asks what flows exist
- asks about a run's status, steps, artifacts or lifecycle
- needs to understand why a flow run failed
- needs the relationship between Run / Step / Artifact / Result

## Inputs

- a flow spec path known to the Platform (never a raw tool command)
- a `run_id` when inspecting an existing run
- the design/top-level name when required by the flow spec

## Procedure

1. If the user asks to execute: call `run_flow` with the flow spec path.
   This is a HIGH-risk tool: it pauses for human approval before execution.
2. If the user asks about an existing run: call `get_run` first and read
   `lifecycle_status`, `execution_status`, `engineering_status`, metrics
   and diagnostics.
3. Call `get_artifacts` to see the recorded evidence (roles, sizes,
   SHA-256) - never guess an artifact's content from its name.
4. Report what the Platform recorded; if a run is missing, say so.

## Tool guidance

Preferred tools:

- `run_flow` (HIGH risk, requires approval)
- `get_run`
- `get_artifacts`

Never invent shell commands, never call EDA tools directly, never read
the Platform filesystem.

## Interpretation

- `lifecycle_status`: where the run is in its lifecycle (created / running /
  success / failed).
- `execution_status`: did the tool process run? `unknown` means it never ran.
- `engineering_status`: the QA verdict; `unknown` until a rule pack is
  evaluated. Tool success is NOT engineering success.
- A nonzero exit code is a recorded fact, not an exception.

## Limitations

Flow execution requires the Platform's EDA environment (Docker). When the
environment is unavailable the Platform answers with a structured error;
report that honestly instead of pretending a flow ran.
