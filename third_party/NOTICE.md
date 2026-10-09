# Third-Party NOTICE

## Vendored source code

No third-party source code is vendored in M0.

Runtime dependencies are installed via pip and are not redistributed in
this repository:

| Package | License | Notes |
|---|---|---|
| PyYAML | MIT | YAML parsing for configs |
| jsonschema | MIT | JSON Schema (draft 2020-12) validation |
| fastapi | MIT | M6.1 Platform/Agent HTTP transport (optional `server` extra) |
| uvicorn | BSD-3-Clause | ASGI server for the HTTP transports |
| httpx | BSD-3-Clause | Platform API client; MCP server → Platform HTTP calls |
| pydantic | MIT | Tool argument schemas the MCP SDK derives (M7.2) |
| mcp (official MCP Python SDK) | MIT | M7.2 MCP transport, exactly one implementation; not vendored |
| langgraph | MIT | M6.5 agent orchestration (Agent Backend) |

## Reference projects (analysis only — no code or text copied)

The projects below were studied for architecture analysis (see
`docs/01-opensource-analysis-and-architecture.md`). This repository
contains **no** source code, README text, or other substantial text from
them.

| Project | URL | License | How it may be used here |
|---|---|---|---|
| Auto-EDA | https://github.com/amir-ghz/Auto-EDA | Custom attribution license (`LicenseRef-Auto-EDA-Attribution`, non-OSI) | Design ideas only. No text or code copying; rewrite everything from scratch. |
| OpenADA | https://github.com/simra-tech/OpenADA | MIT | Design ideas; the planned result-envelope semantics are modeled on it. No code vendored yet — any future vendored OpenADA code must be registered in this file together with its original copyright notice. |
| eda-report-parser | https://github.com/luaiuyuki/eda-report-parser | None (all rights reserved) | Ideas only. Clean-room rule applies: its source must not be read while implementing our parsers, and no code may be copied. |

## Runtime tool images

The platform executes third-party EDA tools inside pinned containers. The
tools are never linked into, redistributed by, or copied into this
repository's source tree.

| Tool image | Upstream | License | Invocation |
|---|---|---|---|
| `aemp-eda-base:0.1.0` (Debian bookworm + Yosys) | Yosys 0.23 via Debian bookworm | ISC (Yosys) | separate process in container |
| `aemp-eda-opensta:3.1.0` (wrapper over `openroad/opensta@sha256:d5aa34ee…`) | OpenSTA 3.1.0 (parallaxsw/OpenSTA, built by the OpenROAD project) | GPL-3.0 | separate process in its own container; GPL does not affect this repository |

## Demo Liberty library (extracted at runtime, not committed)

`examples/sta/` uses `Nangate45_typ.lib` (134 standard cells, Copyright
2004-2011 Nangate Inc.) as a **demo standard-cell library only**. The file
is extracted at runtime from the pinned `aemp-eda-opensta` image
(`/OpenSTA/test/nangate45/Nangate45_typ.lib`) and is gitignored — this
repository does not redistribute it. It is a demo/TOY library for
verifying the methodology automation chain and represents **no commercial
PDK**.
