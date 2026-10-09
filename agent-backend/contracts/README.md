# Agent Backend contracts

This directory intentionally contains **no schema copies**.

The normative contracts live in the shared repository directory
[`../../contracts/`](../../contracts/):

| Contract | Version (pinned) | Consumed by |
|---|---|---|
| `platform-api.yaml` | 1.0.0 | `agent_backend.platform.PlatformClient` (responses validated against `schemas/*.json`) |
| `agent-api.yaml` | 1.1.0 | `agent_backend.api.http` (served as `/openapi.json`); M6.6 added `/api/v1/chat/resume`, `status=interrupted`, approval error codes |

Rules:

1. Never vendor a copy of a contract here — copies drift.
2. `AgentConfig.contracts_dir` / `AEMP_CONTRACTS_DIR` points at the shared
   directory (default: `<repo>/contracts`).
3. When the Agent Backend is split into its own repository, point
   `AEMP_CONTRACTS_DIR` at the platform contracts checkout and bump the
   pinned versions above in the same change.
