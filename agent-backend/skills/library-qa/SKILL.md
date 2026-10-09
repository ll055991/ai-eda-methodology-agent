---
name: library-qa
description: Library and PDK QA concepts and future extension guidance.
version: "1.0"
tags:
  - library
  - pdk
  - liberty
  - lef
  - 库
---

# Library QA

## When to use

Use this skill when the user asks about:

- standard-cell library or PDK quality checks
- Liberty / LEF / GDS consistency at a methodology level
- what a future Library QA capability would validate

## Current scope

This skill documents **concepts and future extension guidance only**.

Not currently implemented:

- real foundry PDK validation
- commercial standard-cell library validation
- Liberty production validation
- LEF/DEF production validation
- DRC/LVS signoff

Do not claim these capabilities and do not call tools that do not exist.

## Inputs

- the library/PDK name and version the user is asking about
- (future) recorded library artifacts with SHA-256 evidence

## Procedure (methodology, not execution)

1. Clarify which library artifact the user means (Liberty, LEF, GDS).
2. Explain what would be checked and why it matters.
3. State explicitly that the Platform cannot yet validate production
   libraries; only the concepts below are in scope.
4. If the user needs real evidence today, point them to run artifacts and
   QA rule packs, which are the implemented mechanisms.

## Future checks (planned, not implemented)

- Liberty: corner completeness, unit/time_unit consistency, cell and pin
  naming, timing model sanity
- LEF/GDS: layer consistency, macro/pin definitions, naming
- Metadata: version, checksums, provenance of the library source

## Tool guidance

No library-specific tool exists in the allowlist. Use only:

- `get_run`
- `get_artifacts`
- `run_qa`

## Limitations

Never present demo libraries (e.g. the Nangate45 demo Liberty shipped for
tests) as a foundry PDK or a production-qualified library.
