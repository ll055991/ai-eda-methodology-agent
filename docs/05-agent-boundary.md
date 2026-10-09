# Agent 边界：可以做什么、不可以做什么（M6.0）

> 状态：仅边界与扩展点设计；Agent Backend 在 M6.2+ 实现，M6.0 不写任何 Agent 代码。
> 相关：[docs/03-architecture.md](03-architecture.md)、[docs/04-api-boundary.md](04-api-boundary.md)

## 1. Agent 可以 / 不可以

| Agent 可以做 | Agent 不可以做 |
|---|---|
| 理解自然语言意图、规划步骤 | import Platform 内部模块（`aemp.flow` / `aemp.drivers` / `aemp.runs` / `aemp.api`） |
| 通过 allowlist Tool 调用 Platform API | 自己拼 Yosys/OpenSTA 命令或调用 Docker/shell |
| 读取 run / metric / finding / artifact 元数据并解释 | 生成、修改或"估算"任何测量值（Metric 只能来自带 SHA-256 的 artifact） |
| 引用 `run_id` 解释 QA 结论 | 直接修改 run.json / SQLite / QA 判定 |
| 失败后重试/换策略（未来，受 Policy 约束） | 提供任意文件读取、任意命令执行能力 |
| 请求人工审批（未来 M6.7） | 绕过审批直接执行高风险动作 |

## 2. Tool Boundary（规范性）

未来 Agent 的工具只能是以下四个，每个映射到一个 Platform API endpoint
（定义在 `contracts/agent-api.yaml` 的 `x-agent-tools`，contract test 强制校验）：

```text
Agent Tool            →  Platform Endpoint
run_flow              →  POST /api/v1/flows/run
get_run               →  GET  /api/v1/runs/{run_id}
run_qa                →  POST /api/v1/runs/{run_id}/qa
get_artifacts         →  GET  /api/v1/runs/{run_id}/artifacts
```

调用链必须是：

```text
Tool → PlatformClient → Platform API（HTTP Contract）→ Platform Backend
```

禁止：

```text
Tool → FlowEngine / YosysDriver / Docker / SQLite     ❌
```

工具边界 = 能力边界：没有 shell 工具、没有任意文件工具、没有"通用 HTTP"工具。

## 3. Tool Gateway 与 MCP 的位置（预留，M6.4 / M6.11）

```text
Agent
  ↓
Tool Gateway（统一注册、参数校验、审计日志、Policy 检查）
  ├── Local Tool（进程内 PlatformClient）
  ├── HTTP Tool（直连 Platform API）
  └── MCP Tool（M6.11：把 Platform 暴露成 MCP Server）
```

要点：**MCP 是 Tool 接入协议，不是 Agent Runtime**。Agent 架构不得与 MCP
强绑定；MCP 只是 Gateway 的一种传输形态，替换它不影响 Agent Loop。

## 4. Skill 与渐进式披露（预留，M6.9 / M6.10）

```text
Skill Registry → Skill Index → load_skill("timing_analysis")
    ↓ Level 1: Skill Metadata（名称/描述/触发条件）
    ↓ Level 2: Skill Instructions（方法论文本，如何时看 WNS/TNS）
    ↓ Level 3: Tools / Resources（允许的工具与参考资料）
```

纪律（沿用 docs/01 AD7）：Skill 只承载方法论知识，**不得重定义断言、
不得改写 Platform Contract、不得绕过 Tool Boundary**。

## 5. Human Approval（预留，M6.7 / M6.8）

```text
Agent → Tool Call → Policy Engine → Risk Check
    ├── 低风险 → 直接执行
    └── 高风险 → INTERRUPT → Frontend 审批 → Approve/Reject → Resume → Tool
```

契约已预留字段（`agent-chat-response.json`，当前不产生实际业务逻辑）：

```text
status: "pending_approval"
approval_required / approval_id / resume_token
```

## 6. RAG / LangSmith / 诊断的位置（预留，M6.12 / Phase 2）

| 能力 | 位置 | 约束 |
|---|---|---|
| RAG（方法论文档、symptom→fix 知识库） | Agent Backend 内部，读 `docs/` 与知识文件 | 不得把检索内容当作测量值；数值必须回到 run artifact |
| Diagnosis（为什么失败） | Agent Backend（M6+ / Phase 2） | 输入是 qa.json / 日志 / metric，输出是假设；不改 Platform 数据 |
| LangSmith / Evaluation | Agent Backend 的观测层 | 只记录 Agent 请求/响应/Tool 调用轨迹 |

## 7. LLM Boundary（P5）

- LLM 永远不进入 Platform 的确定性路径（Flow / Parser / QA / Run 归档）。
- LLM 的每个动作都必须落成一次受控 Tool 调用，并关联到 `run_id`（P7）。
- 数值、状态、判定一律来自 Platform 归档；LLM 只能引用，不能发明。
