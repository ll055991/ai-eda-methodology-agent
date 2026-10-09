# API 边界：谁可以调用谁（M6.0 + M6.1 + M7.2）

> 相关：[docs/03-architecture.md](03-architecture.md)、[docs/06-contracts.md](06-contracts.md)、[docs/07-platform-server.md](07-platform-server.md)、[docs/12-mcp.md](12-mcp.md)

## 1. 调用矩阵

| 调用方 → 被调用方 | 允许 | 通道 |
|---|---|---|
| Frontend → Agent Backend | ✅ | Agent API Contract（HTTP；SSE 后续） |
| Agent Backend → MCP Server | ✅ | MCP（Streamable HTTP；agent allowlist 内的 4 个 Tool + 只读 Resource） |
| Agent Backend → Platform Backend | ✅ | Platform API Contract（HTTP，经 PlatformClient；MCP 关闭时的本地 Tool 路径） |
| MCP Server → Platform HTTP Server → PlatformApi | ✅ | M7.2：MCP Server 只经 `contracts/platform-api.yaml` 调用平台 |
| Platform Backend → EDA 工具 | ✅ | `ToolDriver.execute`（容器内子进程） |
| Frontend → Platform Backend | ❌ | 不直接调用（避免绕过 Agent 的会话/审计） |
| Frontend → EDA / SQLite / 文件系统 | ❌ | 严禁 |
| Agent → Platform 内部模块 | ❌ | 严禁 `import aemp.flow` / `aemp.drivers` / `aemp.runs` / `aemp.api` / `aemp.mcp` |
| Agent → Docker / shell / 任意文件 | ❌ | 契约中不存在此类 Tool（见 agent-api.yaml 的 x-agent-tools） |
| MCP Server → EDA / Docker / shell / subprocess | ❌ | 严禁：MCP 是能力协议，不是执行通道 |
| MCP Server → SQLite / `runs/` / 任意文件 | ❌ | 严禁；Resource 仅限白名单根目录内的 Markdown |
| MCP Server → PolicyEngine / Approval / HITL | ❌ | 权限判定属于 Agent Runtime，不属于能力提供方 |
| MCP Client → 任意 Tool | ❌ | 只有 allowlist（代码常量，对齐 x-agent-tools）内的 Tool 可被调用 |
| Platform → Agent / MCP Server | ❌ | Platform 不认识 Agent，也不认识 MCP，独立运行 |
| 任何人 → 任意文件路径 | ❌ | Artifact 只能经 Run 注册表 allowlist 读取 |


## 2. Platform API（contracts/platform-api.yaml）

| Method | Path | Request | Response | 主要错误码 |
|---|---|---|---|---|
| POST | `/api/v1/flows/run` | `flow`（spec 路径）+ 预留 `inputs` | run summary | FLOW_NOT_FOUND / FLOW_VALIDATION_FAILED / TOOL_ENVIRONMENT_UNAVAILABLE / INVALID_REQUEST |
| GET | `/api/v1/runs` | query: `limit` / `offset` / `status` | run 分页列表 | INVALID_REQUEST |
| GET | `/api/v1/runs/{run_id}` | — | run detail（双状态 + metrics + artifacts + diagnostics + provenance） | RUN_NOT_FOUND |
| POST | `/api/v1/runs/{run_id}/qa` | `rule_pack`（YAML 路径） | QA 结果（PASS/FAIL/UNKNOWN + findings） | RUN_NOT_FOUND / INVALID_REQUEST / QA_EVALUATION_FAILED |
| GET | `/api/v1/runs/{run_id}/artifacts` | — | artifact 元数据列表 | RUN_NOT_FOUND |
| GET | `/api/v1/runs/{run_id}/artifacts/{artifact_id}` | artifact_id = run 内相对路径 | artifact 元数据 | ARTIFACT_NOT_FOUND |

语义要点：

- **失败的执行是 200，不是 HTTP 错误**：`POST /flows/run` 返回的
  `status=failed` 是一个被记录的运行事实；只有"平台/环境无法受理请求"
  才是错误响应。
- **双状态不可合并**：`execution_status` 与 `engineering_status` 是独立字段
  （`contracts/schemas/run.json` 强制 required）。
- **QA 层不重算**：`POST /qa` 必须委托已有 `QAService → QAEngine`；API 层
  不含任何阈值判断逻辑。

### 2.1 MCP Tool ↔ Platform API 映射（M7.2）

MCP 是 Agent 面向的协议，REST 是应用面向的协议，二者是**转换关系**而非替代：

| MCP Tool | Platform API | 说明 |
|---|---|---|
| `run_flow` | `POST /api/v1/flows/run` | 请求体只有 `flow`（+ 预留 `inputs`） |
| `get_run` | `GET /api/v1/runs/{run_id}` | 返回 run detail 原文 |
| `run_qa` | `POST /api/v1/runs/{run_id}/qa` | `rule_pack` 路径，判定由 Platform QA 引擎给出 |
| `get_artifacts` | `GET /api/v1/runs/{run_id}/artifacts` | 仅元数据，无文件内容 |

失败时 MCP 侧 `isError=true`，`structuredContent` 直接携带 Platform 的
错误契约（`RUN_NOT_FOUND` / `FLOW_NOT_FOUND` / `TOOL_ENVIRONMENT_UNAVAILABLE`…），
保证"平台判定"在整条链路上不被改写。

## 3. 安全边界（Trust Boundary）

| 风险 | 防护 |
|---|---|
| 路径穿越（`run_id=../../etc`） | `run_id` 先按 `^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$` 校验，再拼路径（`src/aemp/api/services.py::_read_envelope`） |
| 任意文件读取 | Artifact 只允许命中该 run `run.json` 中已注册的 `artifacts[]`；其余一律 `ARTIFACT_NOT_FOUND` |
| 任意命令执行 | 契约中不存在命令类字段；Agent 只能"点名" flow spec / rule pack，命令由 Platform 生成 |
| 越权修改 | Platform API 只暴露 Run/QA/Artifact 的读与两个受控动作（跑 flow、跑 QA）；无写文件/写库接口 |
| 未知字段注入 | 所有 DTO `additionalProperties: false`，未知字段一律拒绝（contract test 覆盖） |
| MCP 越权工具（M7.2） | Agent 侧 allowlist 是代码常量并与 `x-agent-tools` 对齐；Server 多暴露的工具只被记录、不被暴露给模型 |
| MCP 资源路径穿越（M7.2） | URI 按段校验（拒 `..`/`\`/`%`/盘符/空段），解析后再验证仍在白名单根目录内，并限制单文件大小 |
| MCP 绕过 HITL（M7.2） | 执行路径固定为 Policy → interrupt → approve → Gateway；MCP Server 不持有 PolicyEngine/ApprovalManager |
| MCP 静默降级（M7.2） | 不实现自动 fallback：Server 不可用即结构化失败（`MCP_SERVER_UNAVAILABLE` 等），绝不偷偷切回本地 Tool |

## 4. 错误契约与 HTTP 映射

错误体（`contracts/schemas/error.json`）：

```json
{ "error": { "code": "RUN_NOT_FOUND", "message": "Run not found: ...", "details": {} } }
```

| code | HTTP | 场景 |
|---|---|---|
| INVALID_REQUEST | 400 | 请求结构/参数非法（含未知字段、非法 run_id 格式的显式请求） |
| RUN_NOT_FOUND | 404 | run 不存在或 id 非法 |
| FLOW_NOT_FOUND | 404 | flow spec 文件不存在 |
| FLOW_VALIDATION_FAILED | 422 | spec 结构/引用/环/输入文件校验失败 |
| TOOL_ENVIRONMENT_UNAVAILABLE | 503 | Docker daemon 不可达 / 镜像缺失 |
| TOOL_EXECUTION_FAILED | 500 | 驱动层意外失败（非零退出码不算，它是记录结果） |
| QA_EVALUATION_FAILED | 500 | 规则包加载/评估失败 |
| ARTIFACT_NOT_FOUND | 404 | artifact 未在 run 注册表 |
| INTERNAL_ERROR | 500 | 未预期错误 |

调用方只允许依赖 `code`；`message` 仅用于人类调试，不是稳定契约。

## 5. 实现位置（M6.0 + M6.1 + M7.2）

- M6.0：`src/aemp/api/{services,errors,schemas}.py`——`PlatformApi` 应用服务
  + 契约校验 + 稳定错误码；contract tests 直接验证 DTO 与契约一致。
- M6.1：`src/aemp/api/http.py`——FastAPI Transport Adapter，只调用
  `PlatformApi`；负责 JSON↔DTO、`ApiError.http_status` 映射、请求日志、
  未知异常 → 500 `INTERNAL_ERROR`（不回传 traceback）。
  **HTTP Route 不直接调用 Driver / Docker / SQLite / 文件系统**；
  `/openapi.json` 直接发布 `contracts/platform-api.yaml`（设计源），
  不从实现反向生成。
- M7.2：`src/aemp/mcp/`——Methodology MCP Server（`server.py` / `tools.py` /
  `resources.py` / `client.py` / `config.py` / `errors.py`），入口
  `scripts/run_mcp_server.py`；Agent 侧 `agent-backend/src/agent_backend/mcp/`
  （`client.py` / `registry.py` / `gateway.py` / `models.py` / `errors.py`）。
  MCP 层是 Adapter：**契约与内核零改动**，`AEMP_MCP_ENABLED=false` 时
  Agent 行为与 M6.4 完全一致。
- 启动与配置见 [docs/07-platform-server.md](07-platform-server.md) 与
  [docs/12-mcp.md](12-mcp.md) §17/§18。

