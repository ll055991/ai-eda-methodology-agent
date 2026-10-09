# AGENTS.md - Project Constitution & Coding Standard

Hard constraints for every coding agent working in this repository.
MUST / MUST NOT / NEVER are absolute; violations are reverted on review.
If this file conflicts with [docs/01-opensource-analysis-and-architecture.md](docs/01-opensource-analysis-and-architecture.md), fix both together and state the reason in the commit/PR.

**Project**: AI EDA Methodology & QA Platform — a deterministic pipeline (YAML flow spec → Yosys/OpenROAD/OpenSTA subprocess execution → report parsing → rule-based QA → run history) with a thin LLM agent on the edge, never inside the core data path.

## 1. Core Principles

- **Deterministic Core First**: Do not introduce LLMs or prompt chains into the core data path. The pipeline MUST run end-to-end with no LLM present. The agent layer may only act through public CLI commands and MUST NOT import internal modules.
- **Dual-State Separation**: `execution.status: completed` NEVER implies `engineering.status: pass`. Missing or untrusted evidence is `unknown` — never `fail`, never `pass`. A zero exit code is not an engineering verdict.
- **Clean-Room Implementation**: eda-report-parser has NO LICENSE. Do NOT copy its code or class structures. Do NOT read its source while implementing parsers. Implement pure-function parsers from scratch using golden text fixtures.
- **Strict Subprocess Governance**: Never use `shell=True`. All EDA tool calls must use parameterized argv, bounded output capture (max 16MiB, keep head & tail, record total bytes + truncation flag), and explicit timeout (kill the whole process group on expiry). Every launch MUST: probe and record tool name/version/path (never infer a version), run with a whitelisted child environment (closed env policy, no parent-env inheritance), and re-verify input file SHA-256 at launch (TOCTOU guard).
- **Unit & Reconciliation**: All timing metrics must explicitly track units (e.g., ns, ps) — a metric is always (value, unit), and unit conversion happens in exactly one audited place. All regex parsers must reconcile extracted violation counts against tool-reported summary totals; any mismatch → `unknown` + diagnostics. Zero tolerance.

## 2. Tech Stack & Constraints

- Runtime dependencies: `pyyaml`, `jsonschema>=4.18` (keep minimal, zero-dependency parser subsystem). Adding any runtime dependency requires written justification and a lockfile update in the same PR. The parser subsystem is standard-library only. M6.1 justification: the Platform HTTP server lives in an optional `server` extra (`fastapi`, `uvicorn`) — transport adapter only; the deterministic core never imports it, and pyproject.toml is the single dependency source (no lockfile exists in this repo). M7.2 justification: the Methodology MCP Server lives in an optional `mcp` extra (`mcp`, `httpx`, `uvicorn`, `pydantic`) and the Agent Backend declares `mcp` — the MCP spec mandates the official SDK, exactly one implementation, used on the adapter/transport edge only; the deterministic core never imports it.
- Target Platform: Linux (WSL2 for local development). No Windows-native support promises; platform-sensitive code MUST fail loudly, not degrade silently.
- Storage: SQLite (WAL, single writer). The run directory is the source of truth; the database MUST be rebuildable from run directories.
- Rules as data: Flow Specs and QA rule packs are YAML files. No in-house DSL, no rule-engine framework.

## 3. License Hard Rules (highest priority — violation = revert)

| Source | License | Rule |
|---|---|---|
| Auto-EDA (`amir-ghz/Auto-EDA`) | Custom attribution license (non-OSI) | NEVER copy text — including rewritten SKILL.md/README fragments that amount to a substantial portion. Ideas only; rewrite everything. |
| eda-report-parser (`luaiuyuki/eda-report-parser`) | NONE (all rights reserved) | NEVER copy any code, including its 31-line base class. Clean-room only. |
| OpenADA (`simra-tech/OpenADA`) | MIT | Reuse allowed with the original copyright notice; vendored code lives only in `third_party/` and MUST be registered in `third_party/NOTICE.md`. |
| Yosys (ISC) / OpenROAD (BSD-3) / OpenSTA (GPL-3.0) / KLayout (GPL-3.0) | see docs/01 §2.2 | Subprocess invocation only. NEVER link, embed, or copy tool source; never bundle GPL binaries in any distribution. |

- License whitelist for third-party code: MIT / Apache-2.0 / BSD-* / ISC. **No license = zero copying.** Custom/non-standard licenses default to deny.
- Before introducing any third-party code: read its LICENSE in full, record the SPDX identifier, and register the vendored content in `third_party/NOTICE.md` (source URL, commit, license, reason, date, author).
- LLM-generated code is subject to the same rules: review for smuggled GPL/unlicensed fragments; grep-check regexes and table-driven rule data before merge.

## 4. Repository Layout & Dependency Direction

```
configs/flows|rules|tools   Flow Specs / QA rule packs / tool paths & env whitelists (YAML)
src/aemp/core               config, errors, logging, process governance, hashing, schema entry
src/aemp/schema             aemp.result/v0 envelope + JSON Schema
src/aemp/drivers            base + yosys / openroad / opensta / klayout + registry
src/aemp/flow               flow spec model, engine, Tcl template rendering
src/aemp/parsers            base + registry + per-tool parsers (pure functions)
src/aemp/qa                 rule engine, built-in checks, diagnosis (P2), release QA (P2)
src/aemp/runs               run directory, run.json provenance, SQLite index, rebuild-db
src/aemp/api                Platform API application service (M6.0) + HTTP transport (M6.1) — adapter only
src/aemp/mcp                Methodology MCP Server (M7.2) — adapter only: MCP -> Platform HTTP API
src/aemp/agent              thin orchestrator (P2) — public CLI / Platform API contract only
tests/                      unit + integration + fixtures/reports (golden, per tool version)
data/                       synthetic samples ONLY
third_party/                vendored code + NOTICE.md
```

Dependency direction (MUST hold; no cycles, no back-edges):

- `core` depends on nothing internal.
- `drivers`, `parsers`, `runs` → `core`. Drivers additionally reuse the
  executable-discovery/version-probe primitives from `aemp.doctor` (one
  implementation of PATH resolution and UNKNOWN-never-guessed semantics).
- `drivers` MUST NOT import `runs`, `schema`, or `parsers` (envelope
  mapping happens in the flow engine, M3).
- `parsers` MUST NOT depend on `drivers` or `flow` — parsers are pure functions.
- `flow.engine` → `drivers`, `runs`, `core`; `qa` → `parsers`, `runs`, `core` (never `drivers`).
- `api` → `flow`/`qa`/`runs`/`core` (outermost layer, like `cli`); it adds no business logic.
- `mcp` → `core` only, plus the optional MCP SDK/httpx (outermost adapter, like
  `api`/`cli`); capabilities are reached exclusively over the Platform HTTP
  contract, and nothing in the deterministic core imports it.
- `agent` may only act through public CLI commands or the versioned Platform API contract (HTTP); it MUST NOT import internal modules.

## 5. Data Integrity

- Metric/violation values come ONLY from parsing artifacts tagged with SHA-256. NEVER hand-fill, extrapolate, or let an LLM generate "plausible" numbers.
- Parse failure or missing field → `unknown` + diagnostics. No guessed defaults, no silent skips.
- Every QA finding MUST be traceable within 3 hops: finding → rule → metric → artifact(sha256) → run → tool version.
- Result envelope: versioned `aemp.result/v0`, validated against JSON Schema (closed outer object, open typed `data`), all free text bounded (≤ 4000 chars, head+tail preserved on truncation).

## 6. Testing & Definition of Done

- Parsers are pure functions; golden fixtures live in `tests/fixtures/reports/`, organized per tool version.
- New capability → new tests. Parser change → add/update golden samples in the same commit.
- `pytest` + lint MUST pass before commit. Report failures honestly — never skip, xfail, or weaken assertions to get green.
- Integration tests skip loudly when toolchain binaries are absent; `doctor` must report missing or unidentifiable tools instead of guessing.

## 7. Scope & Data Discipline

- Read the MVP scope and its explicit non-goals (docs/01 §6) before starting any task. Out-of-scope requests go to the Phase 2 backlog — do not implement them.
- No NDA data, private PDK data, or real project data in the repository, ever.
- Changes to architecture decisions or MVP boundaries require a same-PR update to docs/01 and this file.
- Changes to this file itself require separate justification in the commit message or PR description.

## 8. Quick Reference

- `aemp.result/v0` envelope semantics (dual-state, bounded text, SHA-256 lineage) are modeled on OpenADA (MIT): semantics reused, implementation written here.
- Skill organization (SKILL.md), symptom→fix tables, and gate reviews follow Auto-EDA's pattern: ideas only, all text written here.
- Text parsing proved fragile in both reference projects; in this repo a text parser without reconciliation + golden tests does not merge.

## 9. M0 Discipline Checklist

Current milestone: **M7.2 — MCP integration complete**. M0/M1/M2.x/M3/M4/M5/M6.0–M6.6/M7.1 are done. Everything below is binding; where an item elaborates §1–§7, both apply.

1. Deterministic kernel first.
2. No LLM in the deterministic execution path.
3. No silent metric modification.
4. Missing evidence = UNKNOWN.
5. execution status != engineering status.
6. Filesystem run artifacts are source of truth — SQLite (introduced in M1) is an index/query cache only, never the source of truth; `rebuild()` must always be able to reconstruct it.
7. Prefer structured tool output over regex.
8. Regex parser must reconcile counts where applicable.
9. No code copying from unlicensed repositories.
10. OpenADA (MIT) may be reused with attribution, registered in `third_party/NOTICE.md`.
11. Auto-EDA custom license: ideas only, no text/code copying.
12. eda-report-parser has no license: clean-room implementation only.
13. No commercial EDA tool claims in the OSS demo.
14. Do not introduce multi-agent architecture prematurely.
15. Do not expand MVP without explicit approval.

M0 explicitly excludes (each lands in a later milestone): LLM/Agent/RAG, SQLite/PostgreSQL, flow engine, Tcl rendering, EDA drivers, report parsers, QA engine, library/PDK managers, web UI, Docker/K8s orchestration. Do not "helpfully" implement any of them ahead of schedule; if a design seems to require it, stop and raise the conflict instead.

## 10. M1 Discipline

Binding for all work on runs, envelopes, and history:

1. Filesystem is Source of Truth — `runs/<run_id>/` holds the real data.
2. SQLite is Index only — deleting it never loses run data; `rebuild()` reconstructs it from run directories.
3. Write order is mandatory: run directory + `run.json` first, index row second — the index must never claim a run whose directory does not exist.
4. Execution status != Engineering status — never merge them into one field.
5. Unknown != Failed — missing evidence is `unknown`, never interpreted as a failure (or as a pass).
6. No fake hashes — a file that was not read carries no `sha256`/`bytes`; missing inputs are recorded with `exists=false` and nothing else.
7. No guessed tool information — tool version is self-reported or `unknown`.
8. No silent schema changes — `aemp.result/v0` evolves by new version, never by editing the bundled schema in place.
9. All run timestamps are UTC ISO 8601 (`2026-10-07T10:30:00Z`); no local time mixing.
10. No commercial EDA claims; no copying unlicensed source (§3 still applies).

## 11. M2.1 Discipline

Binding for all work on the driver layer:

1. Every EDA tool process in this platform is started by `ToolDriver.execute` — no other code path may call `subprocess`.
2. `shell=True` is forbidden forever; a command is always an argv vector whose first element is the program.
3. A driver reports execution facts only: `exit_code == 0` never implies an engineering pass, and `ToolExecutionResult` carries no QA semantics.
4. Only launch problems raise (`ToolNotFoundError`); a nonzero exit code or a timeout is a recorded result, never an exception and never a fake success.
5. Every execution is bounded: explicit timeout (kill child + process group on expiry) and bounded output capture (head/tail windows, exact byte totals, truncation flags) with stdin detached.
6. Version probing follows UNKNOWN-never-guessed: `UNKNOWN` is a valid answer, never converted into a failure or a fabricated number.
7. Concrete drivers (Yosys/OpenSTA/OpenROAD/KLayout) do not exist yet (M2.2) — do not "helpfully" add them in M2.1 work.
8. Envelope mapping (`execution`/`tool` blocks, `timed_out` → diagnostics) happens in the flow engine (M3); the drivers package must not import runs/schema/parsers.

## 12. M2.2 Discipline

Binding for all work on the Docker/EDA environment:

1. The `aemp-eda-base` container is the EDA environment boundary — EDA tools never run on the host.
2. Real EDA execution is allowed only in integration tests (`tests/integration/`) and demo scripts; **unit tests must never require Docker or EDA tools**.
3. Base images are pinned by digest; `latest` is never used as a version identifier.
4. EDA results are never fabricated: version lines, cell counts, and netlists must come from real tool runs, with exit code/stdout/stderr captured to files.
5. Demo designs under `examples/rtl/` are TOY designs — never presented as PDK-qualified or as real chip designs; no NDA/private PDK data enters the image or the repo.
6. The Docker environment is decoupled from RunManager / result envelope / drivers; the Driver→Docker→tool wiring belongs to M2.4.
7. No YosysDriver, no Docker Compose/Kubernetes/orchestration in M2.2; additions require an explicit, documented need.
8. Volume mounts share the project directory only — never host-wide paths (`C:\`, user profiles).

## 13. M2.3 Discipline

Binding for all driver work:

1. Docker is an execution BACKEND, not a tool: `DockerToolExecutor` composes `ToolDriver` (governance reuse); Docker itself is never subclassed as a tool driver, and no code outside `ToolDriver.execute` may start a process.
2. One result type for everything: container and host executions both return M2.1 `ToolExecutionResult`; no `YosysResult`/per-tool result classes.
3. `executable_path` semantics (documented in models.py): the HOST program actually governed — the docker CLI for container runs; container-side identity comes from the driver's version probe. Never conflated.
4. One workspace per execution, mounted at the container workdir; scripts/inputs must live inside the workspace boundary (enforced with `InvalidRequestError`); artifacts land in the workspace on the host.
5. Pre-flight classification before any run: daemon unreachable → `DockerDaemonUnavailableError`, image missing → `DockerImageMissingError` — never lumped into "execution failed".
6. Tool-level failures (nonzero exit, bad script) are recorded results, never exceptions and never environment errors.
7. On timeout: the governed client is killed (M2.1) AND the uniquely-named container is removed best-effort (`docker rm -f`, logged); `--rm` is always used and the host workspace is the only persistence.
8. Driver configuration lives in `configs/tools/yosys.yaml` (pinned image, never `latest`); no tool names/paths hardcoded in Python.

## 14. M2.4 Discipline

Binding for all orchestration work:

1. `service.py` is the ONLY place runs and drivers meet: RunManager must never import drivers, drivers must never import RunManager.
2. Run record methods (`record_tool/record_execution/record_step/add_diagnostic/mark_running`) accept primitives only — ToolExecutionResult → envelope mapping happens in the service.
3. Run lifecycle success/failed mirrors the EXECUTION outcome only; `engineering_status` stays `unknown` until the QA engine (M5). Tool success is never recorded as engineering success.
4. An environment failure fails the RUN while `execution.status` stays `unknown` (the tool never ran) — implemented via `finalize(..., execution_status="unknown")`, recorded with an `environment` diagnostic and re-raised.
5. The run directory IS the workspace: inputs are frozen into `inputs/`, artifacts/logs land in `artifacts/` and `logs/`, all registered through the existing M1 FileRecord mechanisms (no second artifact model).
6. No schema change without backward compatibility + new schema tests; M2.4 required none (step_id was already reserved in M1).
7. SQLite stays an index — no new tables for steps; step identity lives in run.json.
8. `aemp run` is a single-step command, not a Flow Engine: no YAML flow DSL, no multi-step orchestration before M3.

## 15. M2.5 Discipline

Binding for all OpenSTA/STA work:

1. OpenSTA runs only inside the pinned `aemp-eda-opensta` container (GPLv3 tool, subprocess-only, never linked or redistributed).
2. `sta` success (exit 0) says nothing about timing quality: WNS/TNS verdicts require the timing parser (M4) and the QA engine (M5). Until then `metrics` stays empty and `engineering_status` stays `unknown` — even for obviously negative slack.
3. STA needs Liberty-mapped netlists: generic Yosys output (no standard-cell instances) must never be fed to OpenSTA and presented as a working run. Mapped synthesis (`dfflibmap` + `abc -liberty`) is the prerequisite.
4. Demo Liberty files (e.g. Nangate45) are extracted from the pinned image at runtime and gitignored — never committed, never claimed to be a real/commercial PDK.
5. OpenSTA prints reports (and Tcl errors) on STDOUT: the timing report artifact is the archived stdout, and failure evidence may live in the stdout log instead of stderr.
6. Tool commands are verified against the pinned tool version, never assumed: any flag/command (`-f`, `redirect`, …) that the real tool rejects is removed from the code, not worked around.
7. Drivers never call each other: the yosys→STA connection is the flow level's job (RunService demo prep today, Flow Engine in M3).

## 16. M3 Discipline

Binding for all flow work:

1. The Flow Engine orchestrates; RunManager records; drivers execute. The engine never starts a process and never writes run.json/SQLite outside Run's record methods.
2. Flow validation (unknown tools/references, duplicate ids, cycles, missing input files) completes BEFORE run creation and before any EDA tool executes.
3. Step dependencies come from artifact REFERENCES (`${steps.X.outputs.Y}`), never from list order; cycles are rejected with `InvalidRequestError` before execution.
4. Tool dispatch goes through the ToolRegistry — no if/elif on tool names anywhere in the engine.
5. Declared step outputs are validated honestly: exit 0 without the declared artifact fails the flow (`missing_artifact` diagnostic).
6. Fail-fast is the default; no continue_on_error/retry/conditionals until a flow-policy milestone demands it.
7. Flow runs reuse `aemp.result/v0` unchanged: `step_id` reflects the most recently executed step, and per-step summaries live in the open `provenance.steps` area. No `flow.result/v1` without a versioned schema decision.
8. The executed flow spec is frozen into the run archive (`flow.spec.frozen.yaml`) — a run must always answer "what flow produced this?".
9. Flow spec input paths resolve against the invocation working directory; v1 supports whole-value references only (no string interpolation, no conditionals, no loops, no embedded scripts).

## 17. M4 Discipline

Binding for all parser work:

1. Parsers are PURE: report text in, facts out. A parser must never start Docker/tools, touch RunManager/SQLite, or import the flow engine.
2. Ground truth is the platform's own tool output: parser formats are derived from real reports captured by pinned tools (fixtures under `tests/fixtures/reports/`), never from网上 examples or guesses.
3. Metric naming follows `<domain>.<metric>_<unit>` (`sta.wns_ns`, `route.drc_violation_count`) — the unit is always explicit in the name and in the Metric model.
4. Parsers produce facts, never verdicts: no PASS/FAIL/status fields, no thresholds, no WNS judgment — Metric (M4) vs Rule+Finding (M5) stays separated.
5. Missing metric = absent + diagnostic (`metric_unavailable`), never 0; empty/unrecognizable report = `ReportParseError`; missing file = `ReportNotFoundError`. The three must never be conflated.
6. Robust but not gullible: number formats (`+0.29`/`0.290`/tabs) parse fine; structural changes to a report yield UNKNOWN/diagnostics, not invented values.
7. `parse(path)` reads the file once and delegates to `parse_text` (pure) — keep the structure streaming-friendly for large reports.
8. The parser registry is separate from the ToolRegistry (understanding output vs executing tools); v1 registers OpenSTA only.
9. Units must be grounded: the `_ns` label comes from the demo library's `time_unit : "1ns"` — a unit claimed without a source is a violation of the honesty rules.

## 18. M5 Discipline

Binding for all QA work:

1. QA is deterministic: the same metrics + the same rule pack ALWAYS produce the same findings. No LLM, no network, no time, no randomness in the QA path.
2. Metric (fact), Rule (threshold), Finding (verdict) are three separate layers: parsers produce metrics (M4), rules are YAML data, and only the QA engine produces verdicts.
3. Missing metric = `unknown` — never 0, never pass, never fail.
4. `status` (did the rule hold?) and `severity` (how bad if not?) are independent; a failed `warning` does not gate, a failed `error` does.
5. Overall priority: FAIL (any error-severity fail) > UNKNOWN (any unknown) > PASS.
6. Rules are data: a malformed rule is rejected at loading time, never at evaluation time; no rule logic in Python if/else.
7. The executed rule pack is frozen into the run (`qa/rules.frozen.yaml`); historical runs are never retroactively re-judged by newer rules.
8. QA writes the engineering verdict only — `execution.status` is never touched (exit 0 + violated timing = execution success, engineering fail).
9. Findings are new evidence (`qa/qa.json`); they never overwrite the source report and never mix into the envelope `metrics` area.
10. No diagnosis in M5: the engine says WHICH rule failed, never WHY (that is M6+/Phase 2).

## 19. M7.2 Discipline

Binding for all MCP work:

1. MCP is a **Tool Transport**, never a business layer: the MCP server
   (`src/aemp/mcp/`) translates MCP calls into Platform HTTP calls and adds no
   Flow/QA/Parser/Driver logic of its own. `contracts/` and the deterministic
   core are unchanged by M7.2.
2. The MCP server's only capability path is the Platform HTTP API. It MUST NOT
   start subprocesses, touch Docker, open SQLite, read `runs/`, or read
   arbitrary files; MCP resources come from configured read-only roots
   (allowlist + path boundary + name validation + size limit, all enforced
   before any read).
3. The exposed tool set is exactly the four contract tools
   (`run_flow`/`get_run`/`run_qa`/`get_artifacts`); `read_file`/`write_file`/
   `exec`/`sql`/shell/Docker tools are forbidden forever.
4. Tool argument schemas are declared once (handler signatures) and adapted to
   the agent's `ToolDefinition`; there is no second hand-maintained schema.
5. Tool results are structured: `structuredContent` carries the Platform
   document (or the Platform error contract) and `isError` marks failure. Never
   answer with a bare "success", and never re-invent a Platform error code.
6. The agent-side **allowlist is code-owned** and pinned to
   `contracts/agent-api.yaml` (x-agent-tools) by tests. Discovery is not
   authorization: tools a server advertises but the allowlist omits are never
   exposed to the model.
7. **No automatic fallback**: an unavailable/erroring MCP server yields a
   structured `MCP_*` failure (never a raw SDK/httpx exception) and never a
   silent switch to the local tools.
8. Policy/HITL are not bypassable: execution stays
   Policy → (interrupt → human decision) → Gateway, and the MCP server holds no
   PolicyEngine/ApprovalManager. `run_flow` still interrupts before any MCP call.
9. The agent package keeps its isolation: no `import aemp`, no
   `mcp.server`/server-side SDK usage (client half only), and the MCP SDK
   (`mcp`) is the only sanctioned new dependency — one implementation, never two.
10. The legacy local tools (`agent-backend/.../tools/platform/`) stay in place
    for M7.2 (transitional architecture); removal requires a later milestone.


## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: root `CONTEXT.md` + `docs/adr/` (created lazily; missing files are fine). See `docs/agents/domain.md`.
