# Agent Backend（M6.2 – M6.5）

> 实现：`agent-backend/`（独立 package `agent_backend`，独立 `pyproject.toml`）
> 契约：[contracts/agent-api.yaml](../contracts/agent-api.yaml)（Agent API）、
> [contracts/platform-api.yaml](../contracts/platform-api.yaml)（Agent 唯一平台入口）
> 相关：[docs/03-architecture.md](03-architecture.md)、[docs/05-agent-boundary.md](05-agent-boundary.md)
> 状态：M7.1 Skill System 已完成（渐进式加载的 Markdown 知识层，不可执行）

## 1. 定位与 As-Built 架构

```text
Frontend（未来）
      │ Agent API Contract
      ▼
┌──────────────────────────────────────────────┐
│ Agent Backend（agent-backend/，M6.2）         │
│                                              │
│  FastAPI（api/http.py）                       │
│      │                                       │
│  ChatService（application/chat.py）           │
│      │                                       │
│  StubAgentRuntime（无 LLM）                    │
│      │                                       │
│  PlatformClient（platform/client.py，httpx）   │
└──────────────┬───────────────────────────────┘
               │ HTTP（Platform API Contract）
               ▼
Platform Backend（M6.1 HTTP Server → PlatformApi → Flow/Run/QA → EDA）
```

M6.2 交付：`POST /api/v1/chat`（同步 Stub）、`GET /healthz`、`PlatformClient`
（6 个契约方法）、三层错误模型、共享契约校验、真实 HTTP 集成测试。

M6.3 交付：`LLMAgentRuntime` + `ModelProvider` 抽象 + `OpenAICompatibleProvider`
（httpx，无厂商 SDK），`AEMP_AGENT_RUNTIME=stub|llm` 选择，LLM 错误层级与基础
trace metadata。

M6.4 交付：Tool Calling（OpenAI-compatible tools/tool_calls 解析）、
`ToolDefinition/ToolCall/ToolResult`、`ToolRegistry`（allowlist）、
`ToolGateway`（JSON Schema 参数校验）、4 个 Platform Tools（严格按
`contracts/agent-api.yaml` 的 x-agent-tools）、最小 Agent Loop（轮次上限、
顺序多 Tool、错误回喂、32 KiB 结果截断）。LLMAgentRuntime 只依赖
`ToolGateway`，仍不接触 PlatformClient/HTTP/文件系统。

## 2. ① Agent Backend 职责

| 职责 | M6.2 状态 |
|---|---|
| Intent（意图理解） | 预留：未来 LLM/Agent Runtime |
| Session | 最小模型：`session_id` 客户端提供或服务端生成，**不持久化** |
| Agent Runtime | `AgentRuntime` Protocol + `StubAgentRuntime`（M6.2） |
| Tool Gateway | 契约已定义（`x-agent-tools` 4 个 Tool），实现留 M6.4 |
| PlatformClient | ✅ 已完成：唯一 Platform 访问通道（HTTP + 契约校验） |

## 3. ② Agent Backend 不负责

```text
EDA execution（无 subprocess / shell / docker / EDA 工具）
QA judgement（判定只在 Platform QAEngine；Agent 只读、只解释）
Run storage / Artifact storage（文件系统与 SQLite 只在 Platform）
Platform filesystem（不读 runs/、不读 contracts 之外的平台文件）
```

> Agent Backend 只拥有 **Platform API Permission**，没有 EDA Tool
> Execution Permission（§26）。依赖方向：Agent → HTTP → Platform。

## 4. ③ Agent ↔ Platform：HTTP Contract

- 唯一通道：`PlatformClient` → HTTP → `contracts/platform-api.yaml`。
- 禁止 `import aemp` / `from aemp...`（由 `tests/unit/test_isolation.py` 扫描源码强制）。
- 响应不盲信 200：每个响应都对照共享 JSON Schema 校验（`agent_backend/contracts.py`
  只读取 `../contracts/schemas/`，**不复制**；`agent-backend/contracts/README.md`
  只记录 pin 的契约版本）。

### PlatformClient 方法（全部实现）

| 方法 | Platform Endpoint |
|---|---|
| `run_flow(flow, inputs=None)` | POST `/api/v1/flows/run` |
| `list_runs(limit, offset, status)` | GET `/api/v1/runs` |
| `get_run(run_id)` | GET `/api/v1/runs/{run_id}` |
| `run_qa(run_id, rule_pack)` | POST `/api/v1/runs/{run_id}/qa` |
| `list_artifacts(run_id)` | GET `/api/v1/runs/{run_id}/artifacts` |
| `get_artifact(run_id, artifact_id)` | GET `/api/v1/runs/{run_id}/artifacts/{artifact_id}` |

### 错误模型（三层，必须可区分）

| 异常 | 含义 | 例 |
|---|---|---|
| `PlatformApiError` | Platform 返回稳定错误文档（业务事实） | 404 RUN_NOT_FOUND / 422 FLOW_VALIDATION_FAILED / 503 TOOL_ENVIRONMENT_UNAVAILABLE |
| `PlatformConnectionError` | 传输层失败（连不上） | connection refused / timeout / DNS |
| `PlatformContractError` | 响应不符合契约（集成 bug） | 非 JSON / 缺字段 / 未知字段 / 非法 error code |

## 5. Agent API（contracts/agent-api.yaml）

| Method | Endpoint | Request | Response | 状态 |
|---|---|---|---|---|
| GET | `/healthz` | — | `{"status":"ok"}` | ✅ 仅进程存活，不调用 Platform |
| POST | `/api/v1/chat` | `{message, session_id?}` | `{message, status, session_id?, run_id?, approval_required…}` | ✅ 同步 Stub（`status=completed`） |
| GET | `/docs` / `/openapi.json` | — | Agent 契约本体 | ✅ 与 agent-api.yaml 一致 |

配置（环境变量）：`AEMP_PLATFORM_BASE_URL`（默认 `http://127.0.0.1:8000`）、
`AGENT_HOST`（127.0.0.1）、`AGENT_PORT`（8100）、`AGENT_LOG_LEVEL`、
`AGENT_REQUEST_TIMEOUT_S`（30）、`AEMP_CONTRACTS_DIR`。

启动：`python -m agent_backend.api.http` 或
`uvicorn agent_backend.api.http:app` 或 `scripts/run_agent_server.py`。

日志：Agent 记录 `request_id / method / path / status / duration_ms`；
ChatService 记录 `session_id / status / run_id`；PlatformClient 记录
`endpoint / status / duration_ms`。不记录请求体与 Authorization。

## 6. ④ Future extension points（只预留，不实现）

```text
Agent Runtime
      │
      ▼
Tool Gateway（M6.4）           ← Policy / 审计 / 参数校验
   ┌──┴───────────────┐
Platform Tools      MCP Tools（M6.11：Tool Transport，不是 Runtime）
   │                  │
PlatformClient      MCP Client

Skill（M6.9/10）：Agent Runtime → Skill Registry → Skill → Tool Gateway
                  （Skill 是能力说明+策略+知识，不重新实现 Platform API）

HITL（M6.7/8）：Runtime → Policy → INTERRUPT → Frontend Approval → Resume
                 契约字段已预留：status=pending_approval / approval_required /
                 approval_id / resume_token（M6.2 恒为 false/None）

RAG / Diagnosis（M6.12/Phase 2）：Agent 内部检索与假设生成；
                 数值必须回到 run_id / artifact，不得发明测量值

LangSmith（M6.12）：只做 Agent 观测（请求/响应/Tool 调用轨迹）
```

Runtime 替换路线：`StubAgentRuntime`（M6.2）→ `LLMAgentRuntime`（M6.3）→
`LangGraphAgentRuntime`（M6.5）；HTTP 层与 `ChatService` 不变。

## 7. 安全边界

| 面 | 处理 |
|---|---|
| 平台代码 | 禁止 import；源码扫描测试强制（`test_isolation.py`） |
| Shell / subprocess / Docker | 不存在；源码扫描强制 |
| SQLite / runs/ 文件系统 | 不访问；数据只能经 HTTP |
| 任意 Tool | Agent 契约只有 4 个语义化 Tool；无 shell/文件工具 |
| 错误泄漏 | 500 只回 `Internal server error`；traceback 仅服务端日志 |
| 测试隔离 | 集成测试以子进程启动真实 Platform Server，测试不 import aemp |

## 8. 测试

| 套件 | 位置 | 结果 |
|---|---|---|
| PlatformClient 契约/错误 | `agent-backend/tests/unit/test_platform_client.py` | ✅ |
| ChatService / Runtime seam | `agent-backend/tests/unit/test_chat_service.py` | ✅ |
| Agent HTTP / 契约对齐 | `agent-backend/tests/unit/test_agent_http.py` | ✅ |
| 隔离扫描（无 aemp/subprocess/docker） | `agent-backend/tests/unit/test_isolation.py` | ✅ |
| **真实 HTTP** Agent→Platform | `agent-backend/tests/integration/test_agent_platform_http.py` | ✅ 子进程启动 Platform Server |

运行：`cd agent-backend && python -m pytest`（无需 Docker）。

## 9. M6.3 LLM Runtime（Provider 抽象）

```text
ChatService → AgentRuntime → LLMAgentRuntime → ModelProvider
                                                  ↓
                                       OpenAICompatibleProvider（httpx）
                                                  ↓
                                    POST {base_url}/chat/completions
```

| 组件 | 位置 | 说明 |
|---|---|---|
| `ModelProvider` Protocol | `llm/base.py` | 运行时只依赖抽象，不依赖厂商 |
| `OpenAICompatibleProvider` | `llm/openai_compatible.py` | 唯一实现（M6.3）；兼容 OpenAI/DeepSeek/Qwen/本地 vLLM/Ollama 等 |
| `ModelRequest/Response/Usage/Trace` | `llm/models.py` | 可选字段缺失 = `None`（unknown ≠ 0），不伪造 |
| 错误层级 | `llm/errors.py` | Configuration / Authentication / Connection / Timeout / RateLimit / Provider / Response |
| Provider 工厂 | `llm/factory.py` | 未知 provider / 缺配置 → `LLMConfigurationError`，**无 fallback** |
| Runtime 工厂 | `application/factory.py` | `stub` → `StubAgentRuntime`；`llm` → `LLMAgentRuntime` |
| System Prompt | `application/chat.py` | `PROMPT_VERSION = "eda-methodology-assistant/v1"`，明确"不执行工具/EDA" |

语义要点：

- **无 Tool Calling**：模型只接收对话文本、只返回文本；`PlatformClient` 保持独立，
  LLMAgentRuntime 不引用它（隔离测试强制）。
- Provider 失败 → `ChatResponse.status="failed"`（HTTP 200，消息有界且无密钥），
  不崩溃、不静默换 Stub。
- 日志只记录 provider/model/latency/status/request_id/usage；**不记录** prompt、
  回答正文、API key、Authorization。
- API Key 只来自环境变量（`AEMP_LLM_API_KEY`），禁止进入代码/文档/日志/fixture/Git。
- 真实 LLM 冒烟测试为手工脚本 `scripts/test_llm_runtime.py`，**不属于 pytest**。

## 10. M6.4 Tool Calling + Platform Tools

```text
User → LLM → ToolCall → ToolGateway → ToolRegistry（allowlist）
     → Platform Tool → PlatformClient → Platform HTTP API
     → ToolResult → LLM → Final Answer
```

| 组件 | 位置 | 说明 |
|---|---|---|
| `ToolDefinition / ToolCall / ToolResult` | `tools/models.py` | 纯数据；`to_openai_schema()`、`to_message_content()`（32 KiB 截断 + `truncated`） |
| `AgentTool` Protocol | `tools/base.py` | `definition` + `execute(call)` |
| `ToolRegistry` | `tools/registry.py` | 名称唯一、重复注册拒绝、未知 Tool 拒绝（无动态创建） |
| `ToolGateway` / `RegistryToolGateway` | `tools/gateway.py` | 执行前 JSON Schema 校验；未知/非法参数 → 结构化错误结果 |
| Platform Tools | `tools/platform/` | **恰好** `run_flow` / `get_run` / `run_qa` / `get_artifacts`（contract x-agent-tools） |
| Agent Loop | `application/chat.py` | `MAX_TOOL_ROUNDS`（默认 3，`AEMP_AGENT_MAX_TOOL_ROUNDS`）；多 Tool 顺序执行；错误回喂 LLM |

安全边界（代码级，不靠 Prompt）：Registry allowlist + JSON Schema 校验 +
Gateway + Platform API。LLM 无法 shell / docker / sqlite / 读文件 / 任意 HTTP；
唯一出口是已注册 Tool → PlatformClient。Tool 结果与错误均有界（32 KiB /
1000 字符），失败不会崩溃 Agent，超限返回 `status=failed` +
"Agent reached maximum tool-call rounds."。

语义：`status=completed` 表示得到最终回答；工具成功 ≠ Agent 成功；
轮次超限 / Provider 失败 / 无 Tool 可用时的 Tool Call 均 `status=failed`。

## 11. M6.5 LangGraph Agent Runtime

M6.4 的手写循环被替换为显式 StateGraph（`application/langgraph_runtime.py`），
**不新增任何能力**：

```text
START → agent_node → should_continue ──┬──────────→ END
            ▲                          │
            └──────── tool_node ───────┘
```

- `AgentState(messages, tool_round, status, final_message, failure)`；
  节点只返回部分更新，路由只存在于 `should_continue` 一处。
- `agent_node` 只调 `ModelProvider`；`tool_node` 只调 `ToolGateway`
  （allowlist / JSON Schema / 32 KiB 截断 / 错误回喂全部保留）。
- `MAX_TOOL_ROUNDS`（`AEMP_AGENT_MAX_TOOL_ROUNDS`）语义与 M6.4 完全一致。
- 运行时选择：`AEMP_AGENT_RUNTIME=llm` → `LangGraphAgentRuntime`；
  `LLMAgentRuntime` 保留为兼容子类；`stub` 不变。
- 依赖：`langgraph>=1.0,<2`（`langchain-core` 为强制传递依赖；未使用
  langchain Agent API，也未引入 langchain-openai/openai SDK）。
- 详细概念解释见 [docs/09-langgraph-runtime.md](09-langgraph-runtime.md)。

## 12. M6.6 HITL（Human-in-the-Loop）

```text
Tool Call → PolicyEngine → 需要审批？
    ├── no  → ToolGateway
    └── yes → interrupt + InMemorySaver checkpoint
                 → 人工 Approve/Reject（POST /api/v1/chat/resume）
                 → resume → ToolGateway（approve 执行一次 / reject 不执行）
```

- `run_flow` = HIGH（需审批）；`get_run` / `run_qa` / `get_artifacts` = LOW。
  规则在 `policy/rules.py`（代码数据），LLM 无权决定审批。
- `application/approval.py`：`ApprovalRequest` 绑定 `(thread_id, tool_call_id)`，
  仅 `pending` 可决策，防重放（`APPROVAL_ALREADY_RESOLVED`），
  thread 不匹配 → `APPROVAL_MISMATCH`；Manager 不执行 Tool。
- `tool_node` 每次只处理一个待执行调用（interrupt 位置稳定、精确执行一次）；
  checkpoint 记录 messages / tool_round / executed_tool_call_ids。
- 契约最小扩展（agent-api 1.1.0）：`POST /api/v1/chat/resume`、
  `status=interrupted`、error.json 增加 3 个 approval 错误码；platform-api 不变。
- 详细说明见 [docs/10-hitl.md](10-hitl.md)。

## 13. M7.1 Skill System

```text
message → SkillService.select()（tag/关键词，确定性）
        → Level 1 Catalog（恒在）+ Level 2 命中 SKILL.md（≤2 个，<skill> 边界，≤32 KiB）
        → Level 3 references 仅按需（context 只提示文件名）
        → ModelProvider → Tool Calling → ToolGateway（不变）
```

- 内容与代码分离：`agent-backend/skills/<name>/SKILL.md`（数据）+
  `src/agent_backend/skills/`（registry/loader/catalog/service）；
  根目录可用 `AEMP_SKILLS_DIR` 覆盖。
- 四个 Skill：`flow-engineering` / `timing-analysis` / `library-qa` /
  `failure-diagnosis`，各带 `references/*.md`。
- Skill 不可执行、不能改 Tool Registry、不能绕过 PolicyEngine/HITL；
  `run_flow` 仍然 interrupt（专项测试）。
- 详细说明见 [docs/11-skills.md](11-skills.md)。
