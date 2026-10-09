# AGENTS.md — Agent Backend Constitution (M6.2)

Hard constraints for all work inside `agent-backend/`. The parent repository
constitution ([../AGENTS.md](../AGENTS.md)) still applies where relevant.

## 1. Process / Code / Contract Isolation

- The Agent Backend MUST NOT import Platform code — no `import aemp`,
  no `from aemp...`, no `src/aemp` access of any kind.
- The ONLY way to reach the Platform is the HTTP Platform API contract
  (`../contracts/platform-api.yaml`) through `PlatformClient`.
- `agent_backend` is a standalone package with its own `pyproject.toml`;
  it must be able to move to its own repository without refactoring.

## 2. No Execution Capability

The Agent Backend has NO EDA execution permission. Forbidden, forever:

```
subprocess / shell / os.system / docker CLI / SQLite / platform filesystem
```

It holds Platform API permission only; Flow / ToolDriver / Docker / EDA
live in the Platform Backend.

## 3. Contracts Are Shared, Never Copied

- Normative schemas live in `../contracts/schemas/`; this package reads
  them at runtime (`AgentConfig.contracts_dir`, `AEMP_CONTRACTS_DIR`).
- NEVER vendor a copy of a Platform contract into `agent-backend/`.
- `contracts/README.md` records the pinned contract versions.

## 4. Judgment Stays in the Platform

- QA verdicts come from the Platform QA engine. The Agent may read and
  explain findings; it MUST NOT re-implement thresholds or produce
  measurements.
- No numbers without a `run_id` / artifact lineage behind them.

## 5. Milestone Discipline

- M6.2 baseline: `StubAgentRuntime` (kept forever as the no-credential
  runtime). M6.3 adds the LLM runtime behind the `ModelProvider`
  protocol; M6.4 adds bounded tool calling behind the `ToolGateway`
  protocol; M6.5 implements the loop as an explicit LangGraph StateGraph
  (`langgraph_runtime.py`; `LLMAgentRuntime` stays as a compatibility
  subclass). Runtime selection lives only in `application/factory.py`.
- LangGraph is orchestration only: no prebuilt agent APIs
  (`create_react_agent`/`create_agent`), no LangChain agent APIs, no
  LangSmith. `langchain-core` is allowed solely as langgraph's forced
  transitive dependency. The graph never bypasses `ToolGateway`.
- M7.1 Skills: `SKILL.md` files are untrusted Markdown data. The skill
  layer must never execute code, import dynamically, call tools, mutate
  the ToolRegistry, or bypass PolicyEngine/HITL. Skill content and skill
  runtime code stay separated (`skills/` vs `src/agent_backend/skills/`);
  loading is bounded (32 KiB context) and path-safe (no traversal).
- M6.6 HITL: approval is decided by the code-owned PolicyEngine only -
  never by the LLM, never skipped, never auto-approved. High-risk tools
  (`run_flow`) MUST interrupt before `ToolGateway.execute()`. Approvals
  are bound to `(thread_id, tool_call_id)`, single-use, and resolved only
  through the ApprovalManager; the checkpointer is in-memory (process
  lifetime) and no Redis/PostgreSQL may be introduced. The Agent API
  contract stays the source of truth (v1.1.0 adds resume/approval only).
- Tool calling is allowlist-only: the registry contains EXACTLY the
  tools declared in `contracts/agent-api.yaml` (x-agent-tools). There is
  no shell / file / docker / SQL / arbitrary-HTTP tool, and unknown tool
  names are rejected — never dynamically created.
- Tool arguments are validated against the tool's JSON Schema BEFORE
  execution; tool results are bounded (32 KiB, `truncated` flag) and
  tool errors are fed back to the model as structured results.
- The LLM path still has NO direct platform access: runtime → gateway →
  tool → `PlatformClient`. `LLMAgentRuntime` must never reference
  `PlatformClient`/HTTP/filesystem.
- API keys come from the environment only (`AEMP_LLM_API_KEY`); they must
  never appear in code, docs, logs, fixtures or Git. LLM/tool logs carry
  names/ids/status/latency/usage — never prompts, answers, tool
  arguments or headers.
- M7.2 MCP: MCP is a **Tool Transport**, not the agent. The runtime depends on
  the `ToolGateway` protocol only; `MCPGateway` is one implementation of it and
  `AEMP_MCP_ENABLED` (read in `application/factory.py` only) selects it. This
  package uses the MCP SDK's **client half only** — it must never import
  `mcp.server` or expose itself as an MCP server. The allowlist stays
  code-owned (`mcp/registry.py`, pinned to `contracts/agent-api.yaml`
  x-agent-tools by tests): discovered-but-not-allowlisted tools are never
  exposed. There is **no automatic fallback** — an unavailable MCP server is a
  structured `MCP_*` failure, and SDK/httpx exceptions are normalized before
  they can reach the model. `run_flow` still goes PolicyEngine → interrupt →
  approve → gateway, and the MCP server holds no Policy/Approval. MCP results
  reuse the existing `ToolResult` (32 KiB bound preserved); the legacy local
  tools stay in place (M7.2 transitional architecture).
- LangGraph / MCP / Skill / HITL / RAG / memory / SSE / multi-agent
  arrive in later milestones behind the same seams — never by rewriting
  the HTTP layer.
- Tests may launch the real Platform HTTP server (subprocess) or mock the
  HTTP transport; they MUST NOT import Platform modules to construct
  internal state. Provider tests use `httpx.MockTransport` and the literal
  `test-key`.
