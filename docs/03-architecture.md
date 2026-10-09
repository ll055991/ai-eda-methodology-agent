# 三端架构：Frontend / Agent Backend / Platform Backend（M6.0 + M6.1 + M7.2）

> 状态：M6.0 边界与契约 + M6.1 Platform HTTP Server + M6.2–M7.1 Agent Backend
> + M7.2 MCP 接入层已实现
> 上位文档：[AGENTS.md](../AGENTS.md)（硬约束）、[docs/01](01-opensource-analysis-and-architecture.md)（选型与总体架构）
> 相关：[docs/04-api-boundary.md](04-api-boundary.md)、[docs/05-agent-boundary.md](05-agent-boundary.md)、[docs/06-contracts.md](06-contracts.md)、[docs/12-mcp.md](12-mcp.md)

## 1. 目标架构

```text
Frontend（Chat / Dashboard / Approval UI）
    │  HTTP / SSE（Agent API Contract，contracts/agent-api.yaml）
    ▼
Agent Backend（理解 / 规划 / Tool Calling / 解释；Skill + MCP Client + HITL）
    │  MCP（Streamable HTTP）—— 能力协议，Agent-facing
    ▼
Methodology MCP Server（aemp-methodology：Tools + Resources）
    │  HTTP（Platform API Contract，contracts/platform-api.yaml）
    ▼
┌────────────────────────────────────────────────┐
│ Platform Backend                               │
│   Platform HTTP Server（FastAPI，M6.1）         │
│        │  Transport Adapter（不是业务层）        │
│        ▼                                       │
│   PlatformApi（Application Service，M6.0）      │
│        ▼                                       │
│   Flow / Run / Parser / QA / History           │
│        │  ToolDriver.execute（子进程治理）       │
│        ▼                                       │
│   Yosys / OpenSTA / OpenROAD / KLayout（容器）  │
└────────────────────────────────────────────────┘
```

三种"边界"必须继续分清（M7.2）：

```text
MCP                 = capability protocol（Agent 面向的能力接入）
Platform REST API   = business / application boundary（业务与契约）
ToolGateway/Policy  = security boundary（allowlist + PolicyEngine + HITL，仍在 Agent 侧）
```

> **HTTP Layer 不属于 Deterministic Core**：它只做 JSON ↔ DTO 与
> `ApiError → HTTP status` 映射，业务逻辑仍在 PlatformApi 与内核中。
> **MCP Server 同样是 Adapter**：它只把 MCP 调用翻译成 Platform HTTP 调用，
> 不新增 Flow/QA 判断，也不能触达 EDA / Docker / SQLite / `runs/`。

一句话原则：

> **Agent 负责理解、规划、Tool Calling 和解释；Platform 负责确定性执行、解析、QA 和归档。**

## 2. 三端职责

| 端 | 负责 | 不负责 |
|---|---|---|
| **Platform Backend** | Flow 编排、Run 归档、EDA 工具执行（Driver/Docker）、报告解析（Metric）、QA 判定（Finding）、历史/索引 | 自然语言、规划、解释；不依赖 Agent 即可独立运行 |
| **Agent Backend**（未来） | 意图理解、规划、Tool Calling、Observation、解释；Skill/MCP/RAG/Approval 的宿主 | 不直接 import Platform 内部模块；不自己拼 Yosys/OpenSTA 命令；不产生测量值 |
| **Frontend**（未来） | Chat、Run Dashboard、Flow 状态、Timing/QA 展示、Artifact 浏览、Approval UI | 不直连 EDA、不直读 SQLite、不修改 Run、不执行 QA |

Platform Backend 必须能独立存在：没有 LLM、没有 Agent、没有 Frontend 时，
`aemp` CLI 仍可跑通 "flow spec → 工具执行 → 解析 → QA → run 历史" 全链路。

## 3. 架构原则（P1–P8，强制）

| # | 原则 | 含义 |
|---|---|---|
| P1 | Platform Independent | Platform 不依赖 Agent / Frontend；LLM 不在确定性数据路径上 |
| P2 | Agent Decoupled | Agent 不 import Platform 内部实现（`aemp.flow`、`aemp.drivers`、`aemp.runs`…）；只经 Platform API Contract |
| P3 | Frontend Decoupled | Frontend 不直接访问 EDA / SQLite / 文件系统；只经 Agent API |
| P4 | Deterministic Core | Flow / Parser / QA 保持确定性：同输入 → 同输出；无网络、无随机、无时间依赖 |
| P5 | LLM Boundary | LLM 只能通过受控 Tool（allowlist）调用 Platform；无自由命令能力 |
| P6 | No Arbitrary Execution | 不存在 arbitrary shell / docker / 任意文件读取 Tool |
| P7 | Traceability | Agent 的每个事实性结论必须关联 Run / Artifact / Metric / Finding |
| P8 | API First | 三端只通过稳定 Contract 通信；Contract 独立于 Python 实现 |

## 4. 信任边界与数据流

```text
用户 → Frontend → Agent：自然语言（不可信输入）
Agent → Platform：结构化请求（flow 路径 / run_id / rule pack 路径）
Platform → EDA：由平台代码生成的确定性 argv（Agent 不可见、不可改）
EDA → Platform：报告 → Parser → Metric（事实）→ QA → Finding（判定）
Platform → 磁盘：runs/<run_id>/（文件系统 = source of truth）
```

- **事实只能来自带 SHA-256 的 artifact**：Metric 由 Parser 从归档报告产生；
  Agent 无写 metric / 改判定的权限（物理上只能经 API 读）。
- **双状态语义贯穿三端**：`execution_status`（进程跑没跑）与
  `engineering_status`（工程判定成不成立）永不合并；缺证据 = `unknown`。

## 5. 当前仓库映射（M6.0 + M6.1 + M7.2）

暂不拆分仓库，边界先建立；Platform 已是可独立部署的 HTTP 服务，
MCP Server 是它的一个独立进程/适配层：

```text
ai-eda-methodology-agent/           # 当前单仓库 = Platform + MCP Server + contracts
├── contracts/                      # 语言无关契约（M6.0，M7.2 未改动）
│   ├── platform-api.yaml
│   ├── agent-api.yaml
│   └── schemas/*.json
├── src/aemp/                       # Platform 确定性内核（M0–M5，未改动）
│   ├── core/ schema/ drivers/ flow/ parsers/ qa/ runs/
│   ├── api/                        # M6.0 应用层 + M6.1 HTTP transport
│   │   ├── errors.py schemas.py services.py   # PlatformApi（Application Service）
│   │   └── http.py                            # FastAPI Transport Adapter
│   ├── mcp/                        # M7.2 Methodology MCP Server（Adapter）
│   │   ├── server.py                          # MCPServer：tools + resources
│   │   ├── tools.py client.py                 # 4 个能力 → Platform HTTP API
│   │   ├── resources.py config.py errors.py   # 只读资源 + 配置 + 错误
│   │   └── __init__.py
│   ├── service.py
│   └── cli.py
├── agent-backend/                  # Agent Backend（M6.2，独立 package）
│   └── src/agent_backend/          # ChatService + PlatformClient（HTTP only）
│       ├── mcp/                    # M7.2 MCP Client/Registry/Gateway
│       ├── tools/ skills/ policy/ application/ llm/ platform/
├── scripts/run_platform_server.py  # 启动入口（M6.1）
├── scripts/run_mcp_server.py       # MCP Server 启动入口（M7.2）
├── docs/                           # 架构与边界文档（含 07/08/09/10/11/12）
└── tests/                          # 单元 + 集成 + contract + HTTP + MCP tests
```

后续拆分路线：

```text
frontend/ 独立目录/仓库，只依赖 contracts/；Platform 保持当前形态
（HTTP server 即部署边界），Agent Backend 见 docs/08-agent-backend.md。
```

## 6. M6.1 明确不做

Agent、LLM、LangGraph、MCP、Skill、Progressive Disclosure、Human-in-the-loop、
Interrupt/Resume、RAG、Diagnosis、Frontend、Multi-Agent、SSE、WebSocket、
异步 Job、认证（JWT/OAuth/RBAC）。
它们的位置已在 [docs/05-agent-boundary.md](05-agent-boundary.md) 预留，实施按
M6.2 → M6.12 推进。

> 后续落地情况：M6.2–M6.6（Agent/LLM/Tool Calling/LangGraph/HITL）见
> [docs/08](08-agent-backend.md)–[docs/10](10-hitl.md)；M7.1 Skill 见
> [docs/11](11-skills.md)；**M7.2 MCP 见 [docs/12-mcp.md](12-mcp.md)**。
> 仍未实现：RAG、Diagnosis、Frontend、Multi-Agent、SSE/WebSocket、认证。
