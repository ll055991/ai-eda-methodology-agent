# HITL / Human-in-the-Loop（M6.6）

> 实现：`agent-backend/src/agent_backend/policy/` + `application/approval.py` +
> `application/langgraph_runtime.py`；契约：`contracts/agent-api.yaml` v1.1.0
> 相关：[docs/09-langgraph-runtime.md](09-langgraph-runtime.md)、[docs/08-agent-backend.md](08-agent-backend.md)

## 1. 为什么 EDA Agent 需要 HITL

`run_flow` 不是只读操作：它可能消耗大量计算资源、运行长时间 EDA Flow、
产生设计结果、改变 Run 状态，甚至触发后续流程。因此不能所有 Tool 都自动执行：

```text
LLM 决定 "跑一下 rtl_to_sta"
        ↓
不能直接 ToolGateway → Platform → EDA
```

高风险动作必须有人类确认：

```text
Tool Call → Policy（代码判定）→ 需要审批？
    ├── no  → ToolGateway
    └── yes → interrupt + checkpoint → 人工 Approve/Reject → resume
```

**HITL 只解决"执行某类 Tool 前需要人工确认"，不是 RBAC / 身份系统 / 生产审批流。**

## 2. Low Risk / High Risk

| Tool | Risk | Approval |
|---|---|---|
| `get_run` | LOW | 否 |
| `get_artifacts` | LOW | 否 |
| `run_qa` | LOW | 否 |
| `run_flow` | HIGH | **是** |

规则是代码数据（`policy/rules.py` 的 `DEFAULT_TOOL_POLICIES`），与契约的
`x-agent-tools` 一一对应；不新增 Tool。

## 3. Policy

```text
Tool Call
    ↓
PolicyEngine.evaluate() → PolicyDecision{tool_name, risk_level, approval_required, reason}
```

- 确定性、无 I/O、无 LLM 输入（`LLM = intent`，`Policy = authorization decision`）。
- 未知 Tool：不静默放行（approval_required=False），由 Gateway 以
  `TOOL_NOT_FOUND` 拒绝。
- **审批判断发生在 `ToolGateway.execute()` 之前**——一旦进入 Gateway/Platform
  就可能产生副作用。

## 4. Interrupt

```text
interrupt ≠ exception
```

它表示 **Graph 暂停并等待外部输入**：LangGraph `interrupt(payload)` 抛出
`GraphInterrupt`，HTTP 请求正常返回：

```json
{ "message": "Human approval required for tool 'run_flow'.",
  "status": "interrupted", "approval_required": true,
  "approval_id": "approval_…", "session_id": "…" }
```

实现细节（M6.6）：**每个工具节点调用只处理一个待执行 Tool Call**，因此每个
`interrupt` 在节点中的位置稳定；`thread_id = session_id`。

## 5. Checkpoint

`graph.invoke(...)` 在 interrupt 处结束，之后才有人工决定——必须能恢复
Graph State。M6.6 使用 LangGraph `InMemorySaver`（自定义 dataclass 已在
`allowed_msgpack_modules` 注册）：

```text
interrupt → checkpoint（messages / tool_round / executed_tool_call_ids / thread_id）
```

> **已知限制**：内存 checkpointer 只保证单进程生命周期内可恢复；进程重启
> checkpoint 丢失。生产级持久化留待后续阶段（不引入 Redis/PostgreSQL）。

## 6. Resume

```text
POST /api/v1/chat/resume
{ "session_id": "...", "approval_id": "...", "decision": "approved" | "rejected", "reason": "..." }
```

Approve 语义：

```text
resume → 恢复原 tool_call（不重新问 LLM）→ ToolGateway → Platform
```

Tool 精确执行一次（`executed_tool_call_ids` + 节点返回即提交 checkpoint）。
Reject 语义：

```text
Tool 不执行 → tool message { status: "error", error_code: "APPROVAL_REJECTED" } → LLM 解释
```

**Tool rejected ≠ Agent crashed**：只要 LLM 还能回答，响应是 `completed`。

安全不变量（`ApprovalManager`，内存态）：

- `approval_id` 唯一（`approval_<uuid>`），绑定 `(thread_id, tool_call_id)`——
  不能把 `run_flow(A)` 的批准用到 `run_flow(B)`；
- 只有 `pending` 可决策：`pending → approved | rejected`，重复决策 →
  `APPROVAL_ALREADY_RESOLVED`（409）；未知 → `APPROVAL_NOT_FOUND`（404）；
  thread 不匹配 / 非法 decision → `APPROVAL_MISMATCH`（400）；
- 客户端只能决定**已存在的 pending approval**，不能自己指定 Tool 或声明
  "我批准这个 Tool"；Frontend 仍然只能经 Agent API，不能绕过 Agent 调 Platform；
- ApprovalManager **不执行任何 Tool**。

## 7. 为什么不能让 LLM 决定审批

```text
LLM   = probabilistic（会幻觉、会被 prompt injection）
Policy = deterministic（代码，可审计，可测试）
```

如果由 LLM 判断"这个操作安全，不需要审批"，一次注入即可绕过全部控制。因此：

- 审批决定只在 `PolicyEngine`（代码）里产生；
- System Prompt 只是声明规则，不是安全控制；
- 真正的边界：`Policy → （interrupt/approval）→ ToolGateway → ToolRegistry
  → Platform Tools → PlatformClient → Platform API`。

## 8. As-Built 摘要

| 组件 | 位置 | 说明 |
|---|---|---|
| `ToolPolicy / PolicyDecision / PolicyEngine` | `policy/` | 4 个契约 Tool 的风险分类 + 决定 |
| `ApprovalRequest / ApprovalManager / ApprovalError` | `application/approval.py` | pending/approved/rejected、绑定、防重放、参数摘要（≤1000 字符） |
| Policy + interrupt + checkpointer + resume | `application/langgraph_runtime.py` | `tool_node` 每轮处理一个调用；`ChatService.resume`；`POST /api/v1/chat/resume` |
| 契约变更（最小化） | `contracts/` | agent-api 1.1.0：新增 resume 端点、`status=interrupted`、error.json 增加 3 个 approval 错误码；platform-api 不变 |

## 9. 测试与限制

- 单元：Policy（7）、Approval（13）、HITL runtime（12，覆盖 §36 Tests 1–10）、
  HTTP resume（5）。
- 集成（真实 HTTP）：interrupt 后 Platform **零** `run_flow` 请求 → approve 后
  到达 Platform（本机无 Docker → 503 `TOOL_ENVIRONMENT_UNAVAILABLE`，
  记录为真实结果）；reject 后 Platform 始终零请求。
- 限制：No Redis / No PostgreSQL / No 持久化 checkpointer；No RBAC / 身份认证 /
  多用户；No SSE；`approval_id` 与 checkpoint 仅进程内有效。
