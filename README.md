# AI EDA Methodology & QA Platform

A deterministic, traceable EDA flow automation and QA platform for digital IC
methodology engineers — with an AI agent layer planned on top, never inside
the core execution path.

## 1. Project Overview

The platform (Python package: `aemp`) will drive open-source EDA tools
(Yosys, OpenROAD, OpenSTA, KLayout) through declarative flow specs, parse
their reports into typed metrics, evaluate QA rule packs against those
metrics, and keep a fully traceable, reproducible history of every run.

## 2. Why This Project Exists

Digital IC methodology engineers spend a large share of their time on
non-design work: wiring up flows, eyeballing reports, repeating the same QA
checklists, and chasing tool errors without a shared memory of fixes.
Existing open-source projects solve fragments of this (see
[docs/01-opensource-analysis-and-architecture.md](docs/01-opensource-analysis-and-architecture.md)
for the analysis of Auto-EDA, OpenADA and eda-report-parser), but none
combines: reproducible flow execution, machine-checkable QA rules, per-run
traceability (every number traceable to a hashed artifact), and an agent
layer that can only act through audited, deterministic interfaces.

## 3. Architecture

Layered, deterministic-first design:

```
L5  Agent (thin, later)      orchestrator + RAG via public CLI only
L4  QA & Diagnosis           YAML rule packs, findings, release QA
L3  Report Parsers           typed metrics/violations, reconciliation checks
L2  Flow Execution           flow engine + tool drivers (subprocess governance)
L1  Foundation               schemas, config, hashing, run dirs, SQLite index
```

Core invariants (see [AGENTS.md](AGENTS.md) for the full constitution):

- **Deterministic core first** — no LLM in the data path; the platform runs
  end-to-end without any model.
- **Dual-state separation** — `execution.status: completed` never implies
  `engineering.status: pass`; missing evidence is `unknown`.
- **Filesystem is the source of truth** — every run is a directory of
  hashed artifacts; the database (later) is only an index.
- **Clean-room third-party policy** — see `third_party/NOTICE.md`.

Full analysis and design: [docs/01-opensource-analysis-and-architecture.md](docs/01-opensource-analysis-and-architecture.md).

## 4. Current Status

**M7.2 — MCP integration** (on top of completed M0–M7.1).

Implemented:

- **Platform core (M0–M5)**: run envelopes, driver/Docker governance, Flow
  Engine, OpenSTA parser, deterministic QA rule engine.
- **Platform API (M6.0–M6.1)**: `contracts/platform-api.yaml` + FastAPI
  transport (`src/aemp/api/`), run as `python scripts/run_platform_server.py`.
- **Agent Backend (M6.2–M6.6)**, a separate package under `agent-backend/`:
  chat API, LLM provider abstraction, allowlisted tool calling, a LangGraph
  StateGraph runtime, and human-in-the-loop approval for high-risk tools.
- **Skill System (M7.1)**: `SKILL.md` knowledge selected deterministically
  and injected as bounded context (see [docs/11-skills.md](docs/11-skills.md)).
- **MCP integration (M7.2)**: a *Methodology MCP Server*
  (`src/aemp/mcp/`, name `aemp-methodology`) exposing the four contract tools
  (`run_flow` / `get_run` / `run_qa` / `get_artifacts`) and read-only
  resources (`methodology://skills/...`, `methodology://docs/...`) over MCP
  Streamable HTTP, plus an agent-side MCP client/registry/gateway behind the
  same `ToolGateway` seam. Policy/HITL are unchanged: `run_flow` still
  interrupts for human approval *before* any MCP call. Full design:
  [docs/12-mcp.md](docs/12-mcp.md).

**Not yet implemented** (do not assume they exist):

- Timing arc-level parsing (per-cell delay table)
- More parsers: OpenROAD / KLayout / Yosys stat
- RAG / Memory (M7.3+), failure diagnosis, Library/PDK management
- Frontend, SSE/WebSocket streaming, Multi-Agent, authentication

## 5. Implemented Scope (M0 + M1)

| Component | Location |
|---|---|
| Configuration discovery (`AEMP_PROJECT_ROOT`, `configs/` tree, YAML loading) | `src/aemp/core/config.py` |
| Error hierarchy (`AEMPError` + 5 concrete types) | `src/aemp/core/errors.py` |
| Logging (stderr console handler, run-file seam reserved) | `src/aemp/core/logging.py` |
| SHA-256 helpers (streaming file digest) | `src/aemp/core/hashing.py` |
| Generic JSON Schema validation (draft 2020-12) | `src/aemp/schema/validator.py` |
| `aemp.result/v0` envelope schema + `validate_result()` | `src/aemp/schema/result_v0.json`, `src/aemp/schema/result.py` |
| Run manager (`create_run` / `freeze_file` / `add_input` / `add_artifact` / `finalize`) | `src/aemp/runs/manager.py` |
| Evidence records (path/bytes/sha256, never fabricated) | `src/aemp/runs/models.py`, `src/aemp/runs/artifacts.py` |
| SQLite index (`runs` + `artifacts` tables, `rebuild()` repair) | `src/aemp/runs/index.py` |
| Tool driver base abstraction (`ToolDriver.execute`: governed subprocess, bounded capture, timeout kill; version probe) | `src/aemp/drivers/base.py` |
| Driver value types (`ToolExecutionRequest`, `ToolExecutionResult`) | `src/aemp/drivers/models.py` |
| EDA environment (digest-pinned Debian + Yosys 0.23 container) | `docker/Dockerfile`, `docker/README.md` |
| Docker execution backend (mounts, `--rm` lifecycle, pre-flight classification, timeout cleanup) | `src/aemp/drivers/docker.py` |
| **YosysDriver** (first concrete driver: version probe, workspace boundary, script execution) | `src/aemp/drivers/yosys.py`, `configs/tools/yosys.yaml` |
| **RunService** (M2.4 orchestration: run lifecycle ↔ driver execution; failure recording) | `src/aemp/service.py` |
| **OpenSTA environment + driver** (M2.5: pinned OpenSTA 3.1.0 container, `sta` invocation, STA run step) | `docker/Dockerfile.opensta`, `src/aemp/drivers/opensta.py`, `configs/tools/opensta.yaml` |
| Shared container driver skeleton (workspace boundary, version probe rule) | `src/aemp/drivers/container.py` |
| STA demo chain (mapped synthesis → timing report) | `examples/sta/`, `docker/README.opensta.md` |
| **Flow Engine** (M3: spec loading/validation, topological ordering, cycle detection, ToolRegistry, artifact resolution, fail-fast) | `src/aemp/flow/`, `configs/flows/rtl_to_sta.yaml` |
| **Report parsers** (M4: OpenSTAParser → unit-explicit metrics; registry; real fixtures) | `src/aemp/parsers/`, `tests/fixtures/reports/` |
| **QA rule engine** (M5: `QAEngine` PASS/FAIL/UNKNOWN, YAML rule packs, loading-time validation, frozen rules, `qa/qa.json`, `QAService` engineering verdict) | `src/aemp/qa/`, `configs/rules/base_timing.yaml` |
| RTL demo + synthesis script + Docker demo/env-check scripts | `examples/rtl/`, `scripts/synth.ys`, `scripts/demo_yosys_docker.py`, `scripts/demo_yosys_driver.py`, `scripts/check_eda_env.py` |
| Docker integration tests (real synthesis; skip without Docker) | `tests/integration/` |
| Environment doctor | `src/aemp/doctor.py`, `configs/tools/tools.yaml` |
| CLI (`doctor`, `history`, `run`, `sta`, `parse`, `qa`, `flow run` implemented; `ask` reserved) | `src/aemp/cli.py` |
| Unit tests + GitHub Actions CI (Python 3.11 / 3.12) | `tests/`, `.github/workflows/ci.yml` |

## 6. Installation

Requirements: Python >= 3.11. Target platform is Linux; on Windows develop
under WSL2 (the code itself is platform-neutral, tools are not).

```bash
git clone <this-repo> && cd ai-eda-methodology-agent
python -m pip install -e ".[dev]"
```

## 7. Usage

```bash
aemp --help        # overview; exit code 2 = reserved command
aemp doctor        # environment diagnosis (see below)
aemp --version
```

`aemp doctor` checks Python, OS/architecture, and whether the EDA tools
declared in `configs/tools/tools.yaml` (`yosys`, `openroad`, `sta`, `klayout`)
are on PATH, probing each tool's official version command:

```
AEMP Environment Doctor
=======================

Python
  Version      : 3.11.9
  Status       : OK

Platform
  OS           : Linux
  Architecture : x86_64

EDA Tools

  yosys
    Path       : /usr/bin/yosys
    Version    : Yosys 0.36+db
    Status     : OK
  ...

Overall
  Status       : NOT READY
```

Semantics: a tool is `OK` when its executable is found; the version is the
tool's self-reported string or `UNKNOWN` (never guessed). `Overall` is
`READY` only when all declared tools are found. Exit codes: `0` = success /
READY, `1` = handled error or doctor NOT READY, `2` = command reserved for a
later milestone.

Run from the repository (or set `AEMP_PROJECT_ROOT` to the checkout) so the
config can be located.

### Run history (M1)

```bash
aemp history             # list the 20 most recent runs
aemp history --limit 50
```

```
RUN ID                        STATUS     OPERATION      CREATED
20261007T111724Z-f24214       success    example        2026-10-07T11:17:25Z
```

Every run is a self-contained directory under `runs/<run_id>/`
(`run.json` envelope, `flow.spec.frozen.yaml`, `logs/`, `artifacts/`,
`results/`, `qa/`). The filesystem is the **source of truth**; the SQLite
index (`runs/index.sqlite3`) is only a query cache and can be rebuilt from
the run directories at any time. Each `run.json` is a validated
`aemp.result/v0` envelope in which `execution.status` (did the tool run?)
is independent of `engineering.status` (did the engineering verdict
hold?); missing evidence is `unknown`, never `fail`.

Empty history prints `No runs found.` — never a traceback.

## 8. Testing

```bash
python -m pytest        # unit tests; no EDA tools required
ruff check src tests    # lint
```

Doctor-related tests mock executable discovery and version probes, so the
suite passes on machines without any EDA tooling. CI (GitHub Actions) runs
the same suite on Ubuntu with Python 3.11 and 3.12 and does **not** install
EDA tools.

## 9. Roadmap

| Milestone | Scope |
|---|---|
| M0 (done) | Engineering foundation, `aemp doctor` |
| M1 (done) | `aemp.result/v0` envelope + run directory management + evidence records + SQLite index + `aemp history` |
| **M2.1** (done) | Unified tool driver execution abstraction |
| **M2.2** (done) | Docker + Yosys EDA environment: pinned image, real RTL synthesis demo, integration tests |
| **M2.3** (done) | **YosysDriver**: first concrete driver; Python → Driver → Docker → Yosys → `ToolExecutionResult` end-to-end |
| **M2.4** (done) | **RunService**: RunManager ↔ Driver integration — `aemp run` produces a formal, queryable synthesis run |
| **M2.5** (done) | **OpenSTA environment + driver**: mapped netlist + Liberty + SDC → real timing report |
| **M3** (done) | **Flow Engine**: declarative flow spec → validated, dependency-ordered, fail-fast multi-tool runs |
| **M4** (done) | **EDA Report Parser**: OpenSTA report → unit-explicit structured metrics in the run envelope |
| **M5** (done) | **QA Rule Engine**: metric + rule → deterministic PASS/FAIL/UNKNOWN finding; frozen rule packs; `qa/qa.json` |
| **M6.0–M6.1** (done) | Agent/Platform API contracts + Platform HTTP server (`contracts/`, `src/aemp/api/`) |
| **M6.2–M6.6** (done) | **Agent Backend**: chat API, LLM provider, allowlisted tool calling, LangGraph runtime, HITL approval |
| **M7.1** (done) | **Skill System**: deterministic skill selection + progressive disclosure |
| **M7.2** (done) | **MCP**: Methodology MCP Server (tools + resources) + Agent MCP client/registry/gateway |
| Next | M7.3 Memory (not started); more parsers (OpenROAD / KLayout / Yosys stat) |
| Phase 2 | RAG, failure diagnosis, sweep/brackets, Library/PDK QA, IP release QA, ORFS backend, Postgres, dashboard |

## 10. License / Third-Party Policy

- Project license: not yet decided (TBD).
- Third-party policy: license whitelist (MIT / Apache-2.0 / BSD / ISC), no
  copying from unlicensed repositories, clean-room rules, and vendoring
  registration in [`third_party/NOTICE.md`](third_party/NOTICE.md).
  The full policy is binding for human and AI contributors alike:
  see [AGENTS.md](AGENTS.md) §3.
- EDA tools (Yosys ISC, OpenROAD BSD-3, OpenSTA GPL-3.0, KLayout GPL-3.0)
  are invoked as separate subprocesses inside the EDA container, never
  linked or bundled; the platform does not redistribute GPL binaries.

## 11. EDA Environment (M2.2)

The platform's EDA tools run in a pinned Linux container, not on the host:

```
Windows host (AEMP repo, Docker Desktop)
   │  docker run -v <repo>:/workspace
   ▼
Linux container  (aemp-eda-base:0.1.0 = debian:bookworm-slim @ digest + Yosys 0.23)
   │  yosys -s scripts/synth.ys
   ▼
RTL (examples/rtl/) → synthesis → netlist + logs (runs/demo-yosys/, gitignored)
```

- **Why Docker**: isolates the EDA environment, pins the Linux distro and
  tool versions, removes host dependencies, and makes flows reproducible
  and CI/server-ready.
- **Requirement**: Docker Desktop with the Linux engine running
  (`docker version` must show a Server).
- **Build the image**: `docker build -t aemp-eda-base:0.1.0 docker/`
- **Check the environment**: `python scripts/check_eda_env.py`
- **Verify Yosys**: `docker run --rm aemp-eda-base:0.1.0 yosys -V` →
  `Yosys 0.23 (git sha1 7ce5011c24b)`
- **Run the RTL demo**: `python scripts/demo_yosys_docker.py` — synthesizes
  `examples/rtl/simple_counter.v` (a TOY 4-bit counter, no PDK) and writes:
  - `runs/demo-yosys/logs/yosys.stdout.log` / `yosys.stderr.log`
  - `runs/demo-yosys/artifacts/synthesized.v` (real gate-level netlist)
- **File sharing**: the repo root is mounted at `/workspace` (project
  directory only); outputs come back through the mount.
- **Reproducibility**: digest-pinned base; two synthesis runs produce
  byte-identical netlists (verified, see [docker/README.md](docker/README.md)).
- **Scope**: this is a *Yosys demo environment*, **not** a complete EDA
  flow; the demo design is not a real chip and no PDK is involved.
- Integration tests (`tests/integration/`) run real synthesis when Docker
  is available and skip otherwise; CI currently runs unit tests only.

### YosysDriver (M2.3)

The first concrete driver connects the M2.1 execution abstraction with the
M2.2 environment:

```
YosysDriver (tool identity: image, yosys command, workspace boundary)
   ↓ builds container command
DockerToolExecutor (execution BACKEND: docker run argv, mounts, --rm lifecycle,
   ↓                  pre-flight daemon/image checks, timeout cleanup)
ToolDriver.execute (M2.1 subprocess governance: argv-only, bounded capture,
   ↓                 timeout kill)
docker CLI → container → Yosys → ToolExecutionResult
```

Three roles stay separate on purpose: **Tool** (what the driver wraps —
Yosys), **Executor** (how it is launched — Docker), **Environment** (where
it runs — the pinned image). That is why the flow engine will never hardcode
`docker run`: the executor is swappable (native Linux / Apptainer / remote
later), and no second result type exists (`ToolExecutionResult` covers host
and container runs — for container runs `executable_path` is the host docker
CLI; the container command is visible in `argv`, and Yosys identity comes
from `get_version()`).

```python
driver = YosysDriver.from_config(Path("configs/tools/yosys.yaml"))
version = driver.get_version()                       # "Yosys 0.23 (git sha1 …)"
result = driver.run_script(workspace=ws, script=ws / "synth.ys")
result.status, result.exit_code, result.stdout       # M2.1 facts
```

One workspace per execution (a future run directory plugs in unchanged);
the script must live inside the workspace boundary — enforced. Try it:
`python scripts/demo_yosys_driver.py`.

### First real Run (M2.4)

`RunService` (in `src/aemp/service.py`) is the only place where the run
lifecycle and tool drivers meet — RunManager never imports drivers and
drivers never import RunManager. It sequences: create run → freeze inputs
into the run workspace → record step/tool identity → mark running →
execute via the driver → record execution facts, logs and artifacts →
finalize. Try it:

```bash
aemp run --rtl examples/rtl/simple_counter.v --top simple_counter
```

```
Run ID  : 20261007T140819Z-9fdd69
Status  : SUCCESS
Step    : synth
Tool    : yosys (Yosys 0.23 (git sha1 7ce5011c24b))
Duration: 656 ms
Artifacts:
  - logs/yosys.stdout.log (log_stdout)
  - logs/yosys.stderr.log (log_stderr)
  - artifacts/synthesized.v (netlist)
```

The run is now a formal record: valid `aemp.result/v0` envelope, hashed
inputs/artifacts, SQLite-indexed, visible in `aemp history`. Failure
semantics: a nonzero Yosys exit fails the run with stderr preserved; an
unavailable Docker daemon fails the run while `execution.status` stays
`unknown` (the tool never ran — unknown != failed); the engineering
verdict is always `unknown` until the QA engine (M5).

### STA methodology notes (M2.5)

Static timing analysis needs three inputs, and the demo shows why each
one exists:

```
Netlist (which standard cells, connected by which nets)
+ Liberty (.lib: per-cell/per-pin timing, delay, transition models)
+ SDC (clock definition & period, input/output delays)
       ↓
    OpenSTA → arrival/required time per path → slack → WNS / TNS
```

- **Netlist**: must be a Liberty-MAPPED netlist. The M2.4 generic
  synthesis output has no standard-cell instances (Yosys internal cells
  only) and OpenSTA rejects it — mapped synthesis
  (`dfflibmap` + `abc -liberty`) is the prerequisite.
- **Liberty**: the demo uses `Nangate45_typ.lib` (134 cells), extracted
  from the pinned OpenSTA image at runtime — a **demo standard-cell
  library only**, no commercial PDK, not committed to the repository.
- **SDC**: defines the clock (`create_clock -period 0.5 [get_ports clk]`)
  and input/output delays — checked against the real port names of the
  demo design.
- **OpenSTA exit 0 ≠ timing clean**: even with WNS negative, `sta` exits
  0. The run records execution facts (`timing.rpt` archived, hashed);
  timing verdicts arrive with the parser (M4) and QA engine (M5).

### Flow Engine (M3)

`aemp flow run configs/flows/rtl_to_sta.yaml` executes the whole demo
chain as ONE tracked run:

```text
Flow Spec (aemp.flow/v1 YAML)
   ↓  load + validate: references, duplicate ids, cycles, missing files
   ↓  topological step order (from artifact references, not list order)
   ↓  ToolRegistry: "yosys"/"opensta" → driver + operation (no if/elif)
Run created → flow.spec.frozen.yaml + inputs frozen (hashed)
   ↓
Step synth (YosysDriver)  → artifacts/mapped.v
   ↓  ${steps.synth.outputs.netlist} resolves to the real file
Step sta (OpenSTADriver)  → artifacts/timing.rpt
   ↓
Run finalized → SQLite history
```

Design points:

- **Flow Spec** describes tools/steps/inputs/outputs/dependencies
  declaratively; v1 supports whole-value references only — no conditionals,
  loops, or embedded scripts (it must never become a second language).
- **Validation before execution**: unknown tools, unknown references,
  duplicate step ids, circular dependencies, and missing input files are
  all rejected before a run exists or any EDA tool starts.
- **Artifact references are the dependencies**: `${steps.synth.outputs.netlist}`
  resolves to `runs/<run_id>/artifacts/mapped.v` — the real data interface
  between steps.
- **Fail-fast**: a failed step (or a step that succeeded without producing
  its declared output) stops the flow and fails the run honestly.
- **Freeze**: the executed flow spec and all file inputs are copied into
  the run archive with SHA256 — the archive answers "what exactly ran?".
- **Separation**: Flow Engine orchestrates; RunManager records; drivers
  execute. Flow runs reuse `aemp.result/v0` unchanged (per-step summaries
  live in the open `provenance.steps` area).

### Report parsing & the Metric → Rule → Finding hierarchy (M4)

EDA tools print human-readable reports; automation needs machine-readable
metrics. The parser layer is the bridge:

```text
EDA Tool → text report → Parser → structured metrics → QA (M5)
```

M4 adds `OpenSTAParser` (pure functions — no Docker, no run side effects):

```bash
aemp parse --tool opensta --report runs/<run_id>/artifacts/timing.rpt
```

```text
Metrics:
  sta.wns_ns = 0.29 ns
  sta.tns_ns = 0.00 ns
  sta.path_count = 2 count
  sta.violation_count = 0 count
```

Naming follows `<domain>.<metric>_<unit>` (`sta.wns_ns`,
`place.utilization_pct`, `route.drc_violation_count`) — the unit is part
of the name, so a QA rule can never misread a number. Parsed metrics flow
into `result.metrics` during flow runs.

Three layers that must never be mixed:

| Layer | Example | Milestone |
|---|---|---|
| **Metric** (fact) | `sta.wns_ns = -0.42` | M4 |
| **Rule** (threshold) | `sta.wns_ns >= 0` | M5 |
| **Finding** (verdict) | `timing violation, severity=error` | M5 |

The parser is honest about what it does not know: an empty or
unrecognizable report is a `ReportParseError`; a report missing the WNS
line leaves `sta.wns_ns` absent with a diagnostic — **a missing metric is
never recorded as 0** (unknown != 0). And a negative WNS still produces
no FAIL here: -0.42 is a fact; only M5 rules can turn facts into
verdicts.

### QA Rule Engine (M5)

QA turns facts into verdicts — deterministically, with rules as data:

```text
EDA Tool → Report → Parser → Metric → QA Rule → Finding
```

```bash
aemp qa --run <run_id> --rules configs/rules/base_timing.yaml
```

```text
QA Status: FAIL
Rule pack: base_timing v1
Rules     : 2 total | 0 passed | 2 failed | 0 unknown | 0 warnings
Findings:
  [FAIL   ] timing.wns.non_negative (error): sta.wns_ns = -0.01, expected >= 0.0
```

Semantics:

- **PASS / FAIL / UNKNOWN**: a missing metric is `unknown` — never 0,
  never pass, never fail. Overall priority: FAIL (error) > UNKNOWN >
  PASS; a failed `warning` stays visible but does not gate.
- **status vs severity** are independent: `status` answers "did the rule
  hold?", `severity` answers "how bad is it if not?".
- **Rules are frozen**: the executed pack is copied to
  `qa/rules.frozen.yaml`; a historical run is never re-judged by newer
  rules. Findings are archived as new evidence (`qa/qa.json`) — the
  original report is never overwritten.
- **Execution ≠ engineering**: OpenSTA exiting 0 with WNS = -0.01 stays
  `execution.status: success`, `engineering.status: fail`.
- **No LLM**: same metrics + same rules always produce the same findings.

### Agent Backend & MCP (M6–M7.2)

Three processes, one contract chain:

```text
Agent Backend (:8769)  --MCP/Streamable HTTP-->  Methodology MCP Server (:8770)
                                                        |
                                                        | Platform API (HTTP)
                                                        v
                                                  Platform Backend (:8765)
```

```bash
# 1. platform
AEMP_PORT=8765 python scripts/run_platform_server.py

# 2. methodology MCP server (tools + read-only resources)
AEMP_MCP_PORT=8770 AEMP_MCP_PLATFORM_URL=http://127.0.0.1:8765 \
  python scripts/run_mcp_server.py

# 3. agent backend (LLM runtime + MCP as the tool transport)
AGENT_PORT=8769 AEMP_AGENT_RUNTIME=llm AEMP_MCP_ENABLED=true \
  AEMP_MCP_SERVER_URL=http://127.0.0.1:8770/mcp \
  AEMP_LLM_BASE_URL=<openai-compatible-url> AEMP_LLM_MODEL=<model> \
  AEMP_LLM_API_KEY=<key> python -m agent_backend.api.http
```

Invariants that hold with MCP enabled:

- MCP is a **Tool Transport**, not a new engine: `run_flow` still goes
  through the PolicyEngine (HIGH risk) and still interrupts for human
  approval before any MCP call; `reject` means the Platform is never asked.
- The agent-side allowlist is code-owned (the four `x-agent-tools` names);
  tools a server adds are discovered but never exposed to the model.
- No automatic fallback: an unavailable MCP server yields a structured
  `MCP_SERVER_UNAVAILABLE` failure, never a silent switch to local tools.
- The MCP server has no shell / subprocess / Docker / SQLite / `runs/`
  access — it can only call the Platform HTTP API it adapts.

Design and security model: [docs/12-mcp.md](docs/12-mcp.md).

