# MCP 接入层（M7.2）

> 实现：`src/aemp/mcp/`（Methodology MCP Server）
> + `agent-backend/src/agent_backend/mcp/`（MCP Client / Registry / Gateway）
> 相关：[docs/11-skills.md](11-skills.md)、[docs/10-hitl.md](10-hitl.md)、
> [docs/05-agent-boundary.md](05-agent-boundary.md)、[docs/04-api-boundary.md](04-api-boundary.md)

## 1. MCP 是什么

MCP（Model Context Protocol）是 **能力接入协议**：用统一的 `tools/list`、
`tools/call`、`resources/list`、`resources/read` 把"可执行能力"和"只读知识"
暴露给模型宿主。它规范的是**怎么把能力交出去**，不是业务逻辑。

```text
MCP = Agent-facing capability protocol
Platform REST API = Application-facing business boundary
ToolGateway / PolicyEngine = Security boundary（仍在 Agent 侧）
```

本项目 M7.2 的目标不是"把 Tool 改成 MCP"，而是在 Agent 与 Platform 能力之间
增加标准化接入层，同时**保持** Platform API、ToolGateway、PolicyEngine、HITL
四道既有边界不变。

## 2. 为什么项目需要 MCP

1. **能力来源可替换**：Agent 侧只依赖 `ToolGateway` 协议；本地 Tool 与 MCP
   Tool 是该协议的两种实现，替换不改 Agent Loop、不改 Policy、不改 HITL。
2. **能力边界显式化**：MCP Server 只暴露 4 个契约能力与少量只读资源，
   "Server 新增危险工具 → Agent 自动获得权限"这条路径被 allowlist 切断。
3. **可被外部宿主复用**：任何 MCP 客户端（编辑器、其他 Agent 宿主）都能用同一
   Server 访问平台能力，而不需要 import 平台代码。
4. **资源与能力分离**：Tool 执行、Resource 只读知识，混在一起会让"读取文档"
   与"执行流程"拥有同样的表达力。

## 3. MCP vs Tool Calling（M6.4）

| | M6.4 Tool Calling | M7.2 MCP |
|---|---|---|
| 能力来自 | Python 类（`tools/platform/*.py`） | MCP Server 的 `tools/list` |
| 传输 | 进程内函数调用 | Streamable HTTP |
| Schema 来源 | 手写 `ToolDefinition.input_schema` | Server 公布 → Agent 适配 |
| 执行入口 | `RegistryToolGateway.execute` | `MCPGateway.execute` → `MCPClient` |
| 安全判定 | PolicyEngine + HITL（不变） | PolicyEngine + HITL（**不变**） |

两者**没有取代关系**：`AEMP_MCP_ENABLED=false` 时走的仍是 M6.4 本地 Tool，
M7.2 没有删除 `agent-backend/src/agent_backend/tools/platform/`。

## 4. MCP vs REST API

```text
Agent 说"我要调用 run_flow"
        ↓  MCP（Agent-facing）
MCP Server 把它翻译成一次 Platform HTTP 调用
        ↓  REST（Application-facing）
POST /api/v1/flows/run  →  PlatformApi  →  AEMP Core
```

MCP Server 是 **Adapter**：它不判断 Flow 是否成功、不重算 QA 阈值、不读 EDA 产物。
`contracts/platform-api.yaml` 与 `contracts/schemas/*.json` 在 M7.2 **零改动**。

## 5. MCP Client（Agent 侧）

`agent_backend/mcp/client.py`：SDK 是 async 的，而 Agent 运行时是同步的
（HTTP 层在线程池里跑），因此 `MCPClient` 是一个**同步门面**，内部持有一个
守护线程上的私有 event loop，SDK 的 `Client` 只在那里进入一次：

```python
client = MCPClient(McpServerSpec(name="aemp-methodology", url="http://127.0.0.1:8770/mcp"),
                   connect_timeout_s=5.0, call_timeout_s=30.0)
client.connect()                 # 幂等、线程安全
client.list_tools()              # tools/list
client.call_tool("get_run", {...})
client.list_resources()          # resources/list + resources/templates/list
client.read_resource("methodology://skills/timing-analysis")
client.close()
```

错误规范化（spec §34）：SDK 把失败包装成 anyio `ExceptionGroup`（内含
`MCPError`），这些**永不外泄**，统一映射为：

| 代码 | 含义 |
|---|---|
| `MCP_SERVER_UNAVAILABLE` | 连不上/握手超时（如 `Request 'initialize' timed out`） |
| `MCP_CONNECTION_FAILED` | 握手失败或其他连接层错误 |
| `MCP_CALL_TIMEOUT` | 单次调用超过 `AEMP_MCP_CALL_TIMEOUT_S` |
| `MCP_TOOL_NOT_FOUND` | Server 未暴露该工具 |
| `MCP_TOOL_NOT_ALLOWED` | Server 暴露了，但 Agent allowlist 不允许 |
| `MCP_TOOL_CALL_FAILED` | 调用本身失败（参数非法、handler 崩溃） |
| `MCP_RESOURCE_READ_FAILED` | 资源不可读（未知 URI / 超限） |
| `MCP_INVALID_RESULT` | 结果既无 structuredContent 也无文本 |

超时来源：连接/调用时限由 `MCPClient` 自己用 `future.result(timeout=...)` 强制，
另外把 `call_timeout` 传给 SDK 的 `read_timeout_seconds`。

## 6. MCP Server（Platform 侧）

`src/aemp/mcp/server.py`：Methodology MCP Server。

```text
name      aemp-methodology
version   1.0.0（MCP 接口版本，与平台包版本解耦）
endpoint  http://<AEMP_MCP_HOST>:<AEMP_MCP_PORT>/mcp   （默认 127.0.0.1:8770/mcp）
health    http://<AEMP_MCP_HOST>:<AEMP_MCP_PORT>/healthz（仅进程存活，不代表 Platform/EDA 可用）
transport Streamable HTTP（官方 SDK 自带协议层）
tools     run_flow / get_run / run_qa / get_artifacts
resources methodology://skills/... 、methodology://docs/...
```

启动：

```bash
python scripts/run_mcp_server.py                     # 等价于 python -m aemp.mcp.server
uvicorn aemp.mcp.server:app_factory --factory --host 127.0.0.1 --port 8770
```

Server 的**唯一**能力出口是 Platform HTTP API：

```text
MCP tool → PlatformHttpClient → POST /api/v1/flows/run → PlatformApi → AEMP Core
```

它**没有** subprocess / Docker / SQLite / `runs/` 访问路径（spec §2、§50）。

## 7. MCP Tool

四个契约能力，1:1 映射 Platform API（与 `contracts/agent-api.yaml`
的 `x-agent-tools` 一致）：

| MCP Tool | Platform API | 语义 |
|---|---|---|
| `run_flow` | `POST /api/v1/flows/run` | 执行一个 flow spec，返回 run id 与双状态 |
| `get_run` | `GET /api/v1/runs/{run_id}` | 读 run 的状态/指标/证据 |
| `run_qa` | `POST /api/v1/runs/{run_id}/qa` | 用规则包评估（判定由 Platform QA 引擎给出） |
| `get_artifacts` | `GET /api/v1/runs/{run_id}/artifacts` | 只返回 artifact 元数据 |

Tool 参数 schema 由**处理函数签名**推导（SDK 从 pydantic/`Annotated[..., Field(...)]`
生成 JSON Schema），因此声明只有一处，不存在"两套 schema 手工同步"。

结果形状：`CallToolResult.structuredContent` 携带 Platform 文档原文，
失败时 `isError=true` 且 `structuredContent` 是 Platform 的错误契约
（`RUN_NOT_FOUND`、`FLOW_NOT_FOUND`、`TOOL_ENVIRONMENT_UNAVAILABLE`…）；
若 Platform 不可达则用适配层代码（`MCP_PLATFORM_UNAVAILABLE` /
`MCP_INVALID_PLATFORM_RESPONSE`）。**绝不返回一句 "success"**。

`run_flow` 在 MCP Server 侧**不执行任何 Flow**，它只是发一次 HTTP 请求。

## 8. MCP Resource

只读知识，三个族：

```text
methodology://skills/catalog                      Skill 目录（name/description/version/tags）
methodology://skills/{name}                       该技能 SKILL.md 正文
methodology://skills/{name}/references/{path}     该技能的 references 文档
methodology://docs/{name}                         docs/ 下的方法论文档
```

Resource 来自 M7.1 Skill 系统的**数据文件**（`agent-backend/skills/`，可用
`AEMP_MCP_SKILLS_DIR` 覆盖）与平台 `docs/`（`AEMP_MCP_DOCS_DIR`）。Server 只读
文件，不执行 Skill、不 import Agent 代码。

每个条目都在启动时**具体枚举**（catalog + 每个 SKILL.md + 每个 references 文档
+ 每个 docs 文档），因此不依赖 URI 模板匹配，未列出的 URI 就是不存在；catalog
正文同时列出各技能的 references 文件名，客户端无需猜测即可读取。

安全规则（spec §18）：

- scheme 必须是 `methodology`（`file://` 无法表达）；
- URI 按段解析，`\`、`%`、`..`、`.`、盘符、空段、首尾空白一律在**读取之前**拒绝；
- 解析后的路径必须仍在所属根目录内（`is_relative_to`）；
- 名称必须匹配白名单正则，且文件必须真实存在；
- 单文件大小上限（Skill 正文 32 KiB、其他 64 KiB，`AEMP_MCP_MAX_RESOURCE_BYTES`）。

## 9. MCP Registry（Agent 侧）

`agent_backend/mcp/registry.py`：

```text
MCP Server → tools/list → MCPRegistry → Agent Allowlist → Model Tools
```

- 记录每个 Server 的 `name / endpoint / tools / resources`（多 Server 预留，spec §28）；
- `tools()` 是"发现到的"，`allowed_tools()` 才是"模型能看到的"；
- allowlist 是**代码常量** `DEFAULT_MCP_ALLOWLIST`，并有测试与
  `contracts/agent-api.yaml` 的 `x-agent-tools` 对齐；
- Server 多暴露的工具会被记录并 warn，但**不会**变成可调用工具；
- resource 发现失败不影响 tool 发现（资源是可选知识）。

## 10. MCP Gateway（Agent 侧）

`agent_backend/mcp/gateway.py` 实现既有 `ToolGateway` 协议，因此 Agent Runtime、
PolicyEngine、ApprovalManager、checkpointer **一行都不用改**：

```text
runtime → ToolGateway 协议 ┬─ RegistryToolGateway（本地 Tool，M6.4）
                          └─ MCPGateway（MCP Tool，M7.2）
```

Gateway 负责：发现一次并缓存、allowlist 过滤、参数按发现的 schema 校验、
调用、结果结构化映射、错误规范化、日志（`mcp_call: server/tool/call_id/status/error_code/latency_ms`，
**从不记录参数与正文**）。

选择发生在唯一的地方 —— `application/factory.py`：

```text
AEMP_MCP_ENABLED=true  → MCPGateway
否则                    → RegistryToolGateway（M6.4 行为不变）
```

## 11. MCP 与 Skill 的关系

```text
Skill System ── 告诉 Agent "怎么做"（Markdown，不可执行）
      │
      └── SKILL.md / references  ──→ MCP Resources（只读知识，同一份数据）
MCP Tool ────── 真正调用 Platform（可执行能力）
```

Skill 不需要知道 MCP 存在：工具名保持稳定（`run_flow`/`get_run`/`run_qa`/
`get_artifacts`），Skill 只写"优先使用这些工具"。M7.2 **没有重写** M7.1 任何代码，
只把 Skill 数据同时暴露为 MCP Resource。

## 12. MCP 与 Policy / HITL 的关系

执行路径（spec §25/§26）：

```text
LLM → tool_call run_flow → PolicyEngine（HIGH，代码判定）
      → interrupt()（MCP Server 尚未被调用）
      → 人工 approve → resume → MCPGateway.execute → MCPClient → MCP Server → Platform
      → 或 reject → APPROVAL_REJECTED（Platform 请求数 = 0）
```

- `interrupt` 一定发生在 MCP Tool **真正执行之前**；
- MCP Server **不持有** PolicyEngine / ApprovalManager / HITL（spec §27）：
  权限属于 Agent orchestration/security 层，能力属于 Server；
- 审批绑定仍是 `(thread_id, tool_call_id)`，单次有效，重复审批被拒
  （M6.6 测试全部保持通过）。

## 13. MCP 安全边界

| 对象 | 允许 | 禁止 |
|---|---|---|
| MCP Server | Platform HTTP API、只读资源根 | shell / subprocess / Docker / SQLite / `runs/` / 任意文件 |
| MCP Tool | 4 个契约能力 | `read_file` / `write_file` / `exec` / `sql` / 任意 HTTP |
| MCP Resource | 白名单内的 Markdown | `file://`、`..`、`%`编码绕过、盘符绝对路径 |
| MCP Client | tools/resources 调用 | 绕过 Gateway、绕过 Policy、自动 fallback |
| Agent | `ToolGateway` 协议 | import 平台内部模块、直接跑 EDA |

隔离由测试强制：`test_isolation.py`（agent 包不得 import `aemp`/subprocess/
sqlite3/docker，且 `agent_backend/mcp/` 不得出现 `mcp.server`）。

## 14. 依赖与实现版本

| 项 | 值 |
|---|---|
| SDK | 官方 MCP Python SDK `mcp` 2.3.0（`mcp>=2.3,<3`） |
| Server API | `mcp.server.mcpserver.MCPServer`（2.x 中 FastMCP 已改名；`mcp.server.fastmcp` 会直接抛错） |
| Client API | `mcp.client.client.Client`（URL 字符串即 Streamable HTTP） |
| Transport | Streamable HTTP（`streamable_http_app(streamable_http_path="/mcp")`） |
| 协议版本 | 握手协商（实测 `2026-07-28`） |
| 平台侧依赖 | `pyproject.toml` 的 `mcp` extra（`mcp`/`httpx`/`uvicorn`/`pydantic`）——纯适配层，确定性内核不 import |
| Agent 侧依赖 | `agent-backend/pyproject.toml` 运行时依赖新增 `mcp>=2.3,<3`（只用 client 半边） |

## 15. 为什么没有 MCP → shell

一旦 Server 能开 shell，MCP 就从"能力协议"变成"任意执行通道"：allowlist、
PolicyEngine、HITL 全部失去意义，而 Server 又不在 Agent 的安全层内。
需要新能力时的正确做法是：在 Platform 实现 → 走 Platform API 契约 →
在 MCP Server 增加一个**语义化** Tool → Agent allowlist 显式放行。

## 16. 为什么没有自动 fallback

```text
MCP unavailable → 结构化失败（不偷偷切回 Local Tool）
```

自动降级会造成：行为不一致（同一句话两条执行路径）、Policy 判定不一致
（本地 Tool 与 MCP Tool 的审计/风险标注不同）、权限边界不一致（绕过了
MCP 侧 allowlist）、以及"这次到底走了哪条路"不可追踪。M7.2 明确不实现
自动 fallback；如果未来需要，必须单独设计 Policy。

## 17. 配置

Agent 侧：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AEMP_MCP_ENABLED` | `false` | 关闭时 Agent 完全走 M6.4 本地 Tool，不建立任何 MCP 连接 |
| `AEMP_MCP_SERVER_URL` | `http://127.0.0.1:8770/mcp` | Server 端点 |
| `AEMP_MCP_SERVER_NAME` | `aemp-methodology` | 注册表里的名字（多 Server 预留） |
| `AEMP_MCP_CONNECT_TIMEOUT_S` | `5` | 连接 + initialize 时限 |
| `AEMP_MCP_CALL_TIMEOUT_S` | `30` | 单次调用时限 |

Server 侧：`AEMP_MCP_HOST` / `AEMP_MCP_PORT` / `AEMP_MCP_PLATFORM_URL` /
`AEMP_MCP_REQUEST_TIMEOUT_S` / `AEMP_MCP_SKILLS_DIR` / `AEMP_MCP_DOCS_DIR` /
`AEMP_MCP_MAX_RESOURCE_BYTES`。

`enabled=true` 但 Server 不可用时：Agent **仍能启动**（选择是配置，连接是惰性的），
但每次工具调用都会得到明确的结构化失败并写 ERROR 日志，绝不静默降级。

## 18. 本地三服务演示

```text
Agent Backend   :8769   ← AEMP_MCP_ENABLED=true, AEMP_MCP_SERVER_URL=http://127.0.0.1:8770/mcp
MCP Server      :8770   ← AEMP_MCP_PLATFORM_URL=http://127.0.0.1:8765
Platform        :8765
```

```bash
AEMP_PORT=8765 python scripts/run_platform_server.py
AEMP_MCP_PORT=8770 AEMP_MCP_PLATFORM_URL=http://127.0.0.1:8765 python scripts/run_mcp_server.py
AGENT_PORT=8769 AEMP_AGENT_RUNTIME=llm AEMP_MCP_ENABLED=true \
  AEMP_MCP_SERVER_URL=http://127.0.0.1:8770/mcp \
  AEMP_LLM_BASE_URL=... AEMP_LLM_MODEL=... AEMP_LLM_API_KEY=... \
  python -m agent_backend.api.http
```

## 19. 测试层次

| 层次 | 位置 | 证明什么 |
|---|---|---|
| Server 装配/工具 schema | `tests/unit/test_mcp_server.py` | 只暴露 4 个契约工具、schema 来自签名、失败是结构化 `isError` |
| Platform 客户端与错误映射 | `tests/unit/test_mcp_client.py`、`test_mcp_tools.py` | 端点/请求体/错误码映射 |
| Resource 安全 | `tests/unit/test_mcp_resources.py` | 遍历/编码/绝对路径/超限/未知名称全部读前拒绝 |
| Server↔真实 Platform（真 socket） | `tests/integration/test_mcp_server.py` | MCP 协议 → 真实 Platform HTTP → 真实 run 目录；`run_flow` 无 Docker 时如实 503 |
| Gateway/allowlist/超时（无网络） | `agent-backend/tests/unit/test_mcp_gateway.py`、`test_mcp_client.py` | allowlist、无 fallback、错误规范化、32 KiB 上界 |
| 全链路 + HITL（真 socket） | `agent-backend/tests/integration/test_mcp_http.py` | Agent→MCP→Platform；approve 恰好一次、reject 零次、越权工具被拒 |

Docker 不可用时：MCP 协议与 Platform HTTP 仍然 PASS，真实 EDA 执行按既有
integration 规则 SKIP —— 三者不混为一谈。

## 20. Transitional architecture（M7.2 保留旧 Tool）

M7.2 是过渡态：本地 Tool 与 MCP Tool **并存**，由 `AEMP_MCP_ENABLED` 选择。

```text
Agent Capability Gateway = ToolGateway 协议（既有 seam）
   ├── RegistryToolGateway  （M6.4，默认）
   └── MCPGateway           （M7.2，AEMP_MCP_ENABLED=true）
```

未来若需"同时存在本地 Tool 与 MCP Tool"，需要给能力起不同的工具名并定义合并
Policy；M7.2 不做这件事，也不删除 `tools/platform/`。

## 21. 后续 Multi-Agent 如何复用 MCP

M7.2 只保留一个 Agent，但架构上 MCP Server 与被调用方解耦：

```text
Supervisor Agent ─┬─ Flow Agent ──┐
                  ├─ QA Agent ────┤── 同一个 aemp-methodology MCP Server
                  └─ Diagnosis ───┘        （各自仍需自己的 Policy/HITL）
```

多 Agent 共享的是**能力协议与 Server**，不是权限：每个 Agent 仍各自持有
allowlist 与审批策略。M7.2 不实现 Multi-Agent、Memory、RAG、LangSmith、
Evaluation、WebSocket、Redis/PostgreSQL（spec §50）。
