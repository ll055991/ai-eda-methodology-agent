# AEMP Agent Backend (M6.2 – M7.2)

A standalone service hosting the AI agent layer. M6.2 built the isolated
baseline (chat API + `PlatformClient`); M6.3 added the LLM runtime and
model-provider abstraction; M6.4 added tool calling with a minimal loop;
M6.5 replaced that loop with an explicit **LangGraph StateGraph**; M6.6
added **human-in-the-loop approval**: high-risk tools pause the graph via
`interrupt` + checkpointer and resume only after a human decision; M7.1
adds the **Skill System**: on-demand Markdown knowledge
(`skills/*/SKILL.md`) with progressive disclosure, never executable; M7.2
adds **MCP** as the tool transport: the same four tools are reached through
a Methodology MCP Server, with the local tools kept as the default.

```text
Frontend ──(Agent API Contract)──▶ Agent Backend
                                      │  FastAPI → ChatService
                                      │      ├─ StubAgentRuntime             (AEMP_AGENT_RUNTIME=stub)
                                      │      └─ LangGraphAgentRuntime        (AEMP_AGENT_RUNTIME=llm)
                                      │            │  StateGraph: agent ⇄ tools, conditional edge
                                      │            ├─ ModelProvider → OpenAI-compatible HTTP API
                                      │            └─ PolicyEngine → HITL → ToolGateway (one seam)
                                      │                   ├─ RegistryToolGateway (M6.4 local tools)
                                      │                   └─ MCPGateway → MCPClient          (M7.2)
                                      ▼  MCP (Streamable HTTP) / HTTP (Platform API Contract)
                                   Methodology MCP Server → Platform Backend → Docker / EDA
```

## Isolation rules (enforced by tests)

- No `import aemp` — ever. HTTP contract only.
- No subprocess / shell / docker / SQLite / platform filesystem access.
- Contracts are shared from `../contracts/`, never copied.
- QA judgment stays in the Platform; the agent interprets, never measures.

## Quick start

```bash
# 1) Platform Backend (repo root)
python scripts/run_platform_server.py          # :8000

# 2) Agent Backend (this directory)
python -m agent_backend.api.http               # :8100
# or: uvicorn agent_backend.api.http:app
```

Environment:

| Variable | Default | Purpose |
|---|---|---|
| `AEMP_AGENT_RUNTIME` | `stub` | `stub` \| `llm` (llm without config fails at startup) |
| `AEMP_PLATFORM_BASE_URL` | `http://127.0.0.1:8000` | Platform API base URL |
| `AGENT_HOST` | `127.0.0.1` | bind host |
| `AGENT_PORT` | `8100` | bind port |
| `AGENT_LOG_LEVEL` | `INFO` | log level |
| `AGENT_REQUEST_TIMEOUT_S` | `30` | PlatformClient HTTP timeout |
| `AEMP_CONTRACTS_DIR` | `<repo>/contracts` | shared contract schemas |
| `AEMP_LLM_PROVIDER` | `openai_compatible` | only supported provider (M6.3) |
| `AEMP_LLM_BASE_URL` | — | provider base URL (`.../v1`) |
| `AEMP_LLM_API_KEY` | — | **environment only**, never logged/committed |
| `AEMP_LLM_MODEL` | — | model name |
| `AEMP_LLM_TIMEOUT_S` | `60` | model call timeout (never infinite) |
| `AEMP_LLM_TEMPERATURE` | — | optional, `[0, 2]` |
| `AEMP_LLM_MAX_TOKENS` | — | optional |
| `AEMP_AGENT_MAX_TOOL_ROUNDS` | `3` | hard bound on LLM↔tool rounds |
| `AEMP_MCP_ENABLED` | `false` | use the MCP transport instead of the M6.4 local tools |
| `AEMP_MCP_SERVER_URL` | `http://127.0.0.1:8770/mcp` | Methodology MCP Server endpoint |
| `AEMP_MCP_SERVER_NAME` | `aemp-methodology` | registry name (multi-server ready) |
| `AEMP_MCP_CONNECT_TIMEOUT_S` | `5` | connect + initialize deadline |
| `AEMP_MCP_CALL_TIMEOUT_S` | `30` | per-call deadline |

## Endpoints

| Method | Path | Status |
|---|---|---|
| GET | `/healthz` | liveness of this process only (never calls the Platform) |
| POST | `/api/v1/chat` | synchronous stub reply (`contracts/agent-api.yaml`) |
| POST | `/api/v1/chat/resume` | decide a pending approval (M6.6) |
| GET | `/docs`, `/openapi.json` | the Agent API contract itself |

## M6.3 LLM runtime

```text
ChatService → AgentRuntime → ModelProvider → OpenAI-compatible HTTP API
```

- **Stub mode** (default, no credentials needed): `AEMP_AGENT_RUNTIME=stub`
  → `StubAgentRuntime`.
- **LLM mode**: `AEMP_AGENT_RUNTIME=llm` plus `AEMP_LLM_BASE_URL` /
  `AEMP_LLM_API_KEY` / `AEMP_LLM_MODEL` → `LLMAgentRuntime` +
  `OpenAICompatibleProvider` (httpx, no vendor SDK). Missing configuration
  raises `LLMConfigurationError` at startup — there is **no automatic
  fallback to stub**.
- The runtime is selected only in `application/factory.py`; the HTTP layer
  and `ChatService` never know the provider.
- Errors: `LLMAuthenticationError` / `LLMConnectionError` /
  `LLMTimeoutError` / `LLMRateLimitError` / `LLMProviderError` /
  `LLMResponseError`; a provider failure becomes
  `ChatResponse.status="failed"` (HTTP 200, bounded message, no secrets).
- Manual real-LLM smoke test: `python scripts/test_llm_runtime.py "..."`.
## M6.4 Tool calling (minimal agent loop)

```text
User → LLM → ToolCall → ToolGateway → ToolRegistry (allowlist)
     → Platform tool → PlatformClient → Platform HTTP API
     → ToolResult → LLM → Final answer
```

- **Tools = exactly the contract allowlist** (`contracts/agent-api.yaml`,
  x-agent-tools): `run_flow`, `get_run`, `run_qa`, `get_artifacts`.
  Unknown tool names are rejected (`TOOL_NOT_FOUND`); there is no shell,
  file, docker, SQL or arbitrary-HTTP tool.
- **Arguments are validated against each tool's JSON Schema** before
  execution (`additionalProperties: false`); invalid arguments become a
  `TOOL_ARGUMENTS_INVALID` result, never an execution.
- **Bounded loop**: at most `AEMP_AGENT_MAX_TOOL_ROUNDS` (default 3) LLM
  calls; multiple tool calls in one reply run sequentially. Exceeding the
  limit fails honestly ("maximum tool-call rounds").
- **Tool errors are fed back to the model** as structured results
  (`PLATFORM_API_ERROR` / `PLATFORM_CONNECTION_ERROR` / ...) — the agent
  loop never crashes and never bypasses the gateway.
- **Bounded tool results**: results are truncated to 32 KiB with
  `truncated: true` before reaching the LLM.

## M6.5 LangGraph runtime

```text
START → agent_node → should_continue ──┬──────────→ END
            ▲                          │
            └──────── tool_node ───────┘
```

- The loop is now an explicit `StateGraph` (`application/langgraph_runtime.py`):
  `AgentState`, `agent_node`, `tool_node`, `should_continue`, `compile()`.
  No prebuilt agent API (`create_react_agent`/`create_agent`) is used.
- `AEMP_AGENT_RUNTIME=llm` → `LangGraphAgentRuntime`;
  `LLMAgentRuntime` remains as a compatibility subclass; `stub` unchanged.
- The `ModelProvider` abstraction and the M6.4 tool system are unchanged:
  `tool_node` only talks to `ToolGateway`; `MAX_TOOL_ROUNDS` and all tool
  semantics are preserved.
- See [docs/09-langgraph-runtime.md](../docs/09-langgraph-runtime.md) for
  State / Node / Edge / Conditional Edge explained in one page.

## M6.6 Human-in-the-loop

```text
Tool Call → PolicyEngine (code) → approval required?
    ├─ no  → ToolGateway
    └─ yes → interrupt + checkpoint → human decision → resume → ToolGateway
```

- `run_flow` is HIGH risk (approval required); `get_run` / `run_qa` /
  `get_artifacts` are low risk. The LLM never decides approval.
- An interrupted chat returns `status="interrupted"` + `approval_required`
  + `approval_id`; the tool has NOT executed. `POST /api/v1/chat/resume`
  with `{session_id, approval_id, decision}` resumes exactly that tool call
  (approve → execute once; reject → `APPROVAL_REJECTED` tool result and the
  agent explains).
- Approvals are bound to `(thread_id, tool_call_id)`, single-use
  (`APPROVAL_ALREADY_RESOLVED` on replay), session-bound
  (`APPROVAL_MISMATCH`) and never executed by the manager.
- Checkpointer: LangGraph `InMemorySaver` — resume works within the
  process lifetime only (no Redis/PostgreSQL in this milestone).
- See [docs/10-hitl.md](../docs/10-hitl.md).

## M7.1 Skill system

```text
message → keyword/tag selection → Skill Catalog (always)
        → selected SKILL.md bodies (max 2, <skill> boundaries, ≤32 KiB)
        → references stay on demand (Level 3)
```

- Skills live in `skills/<name>/SKILL.md` (data) and are separate from the
  runtime in `src/agent_backend/skills/` (code). `AEMP_SKILLS_DIR` overrides
  the root.
- Four skills: `flow-engineering`, `timing-analysis`, `library-qa`,
  `failure-diagnosis`; each ships at least one `references/*.md`.
- Skills only extend the system prompt: no execution, no registry access,
  no policy/HITL bypass. `run_flow` still interrupts for approval.
- See [docs/11-skills.md](../docs/11-skills.md).

## M7.2 MCP transport

```text
runtime → ToolGateway ─┬─ RegistryToolGateway → PlatformClient → Platform HTTP   (default)
                       └─ MCPGateway → MCPClient → MCP Server → Platform HTTP    (AEMP_MCP_ENABLED=true)
```

- **MCP is a transport, not a runtime**: the graph, `PolicyEngine`, `ApprovalManager`
  and checkpointer are untouched; `run_flow` still interrupts for approval
  *before* any MCP call, and a rejection means the MCP server is never told.
- **Allowlist stays code-owned** (`mcp/registry.py`, aligned with
  `contracts/agent-api.yaml` x-agent-tools). Tools a server advertises but the
  allowlist does not contain are discovered and logged, never exposed.
- **No automatic fallback**: an unreachable/erroring MCP server produces a
  structured `MCP_SERVER_UNAVAILABLE` / `MCP_CONNECTION_FAILED` /
  `MCP_CALL_TIMEOUT` tool result; the local tools are never silently used.
- **Error normalization**: SDK `ExceptionGroup`/`MCPError` never reach the
  model; Platform codes (`RUN_NOT_FOUND`, `TOOL_ENVIRONMENT_UNAVAILABLE`, …)
  pass through unchanged.
- **Bounded results**: MCP results become the same `ToolResult` (32 KiB
  truncation with `truncated: true`) as the local tools.
- Resources are read-only knowledge (`methodology://skills/...`,
  `methodology://docs/...`); the agent exposes them through
  `MCPClient.list_resources()` / `read_resource()` — they are never executed.
- Configure a running server:
  `AEMP_MCP_ENABLED=true AEMP_MCP_SERVER_URL=http://127.0.0.1:8770/mcp`.
- See [docs/12-mcp.md](../docs/12-mcp.md).

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest            # unit + real-HTTP integration (no Docker, no LLM key)
ruff check src tests
```

## Layout

```text
src/agent_backend/
├── config.py            AgentConfig + LLMConfig + MCPConfig (env-driven)
├── errors.py            Agent API error model
├── contracts.py         shared contract schema loader (read-only)
├── logging.py
├── models/chat.py       ChatRequest / ChatResponse
├── skills/              SkillRegistry + SkillLoader + SkillService (M7.1)
├── mcp/                 MCPClient + MCPRegistry + MCPGateway (M7.2)
├── llm/                 ModelProvider + OpenAI-compatible provider + errors
├── tools/               ToolDefinition/Call/Result + registry + gateway
├── tools/platform/      run_flow / get_run / run_qa / get_artifacts (M6.4)
├── platform/            PlatformClient + error model (HTTP only)
├── application/chat.py  ChatService + AgentRuntime + Stub + compat facade
├── application/langgraph_runtime.py  StateGraph runtime (agent/tools nodes)
├── application/factory.py  runtime selection (stub | llm) + tool transport
└── api/http.py          FastAPI transport adapter
```
