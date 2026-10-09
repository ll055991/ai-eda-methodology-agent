# Library artifacts (reference)

Methodology reference for the `library-qa` skill. Concepts only - no
library validation is implemented in this platform today.

## Artifact types

| Artifact | Contains | Typical QA concerns |
|---|---|---|
| Liberty (`.lib`) | cell/pin timing, power, function | corner completeness, time_unit, naming, missing arcs |
| LEF | physical abstracts, pins, layers | pin/layer consistency with Liberty, naming |
| GDS/OASIS | layout geometry | layer/DRC readiness (not validated here) |
| SDC | clock and constraint definitions | expected clocks, input/output delays |
| Metadata | version, checksums, provenance | traceability to a pinned source |

## What "Library QA" would check (future)

- unit consistency (`time_unit`, `capacitive_load_unit`, voltage domains)
- corner set completeness across a characterization set
- cell naming and prefix conventions
- timing model sanity (monotonic delays, missing timing groups)
- checksums and version pinning for reproducibility

## What this platform supports today

- recording library files as hashed run artifacts (`get_artifacts`)
- timing analysis with a demo Liberty extracted from the pinned OpenSTA
  image (Nangate45 demo, for tests only)

## Hard limits

- No foundry PDK and no commercial library is bundled or validated.
- The Nangate45 demo Liberty is NOT a production-qualified library.
