# LangGraph Agent Runtime（M6.5）

> 实现：`agent-backend/src/agent_backend/application/langgraph_runtime.py`
> 依赖：`langgraph>=1.0,<2`（实测 1.2.14；`langchain-core` 为强制传递依赖，未直接使用其 Agent API）
> 相关：[docs/08-agent-backend.md](08-agent-backend.md)、[docs/05-agent-boundary.md](05-agent-boundary.md)

## 1. 为什么需要 LangGraph

M6.4 的手写循环把控制流藏在一个 `for` 里：

```text
while/for → LLM → tool_calls? → Tool → LLM → ...
```

M6.5 把它显式化为 **StateGraph**：

```text
START → agent_node → should_continue ──┬──────────────→ END
              ▲                        │
              │                        ▼
              └──────── tool_node ─────┘
```

收益：控制流成为可检查的拓扑（谁能到谁、是否条件边），而不是散落在 Python
分支里的隐式约定。**M6.5 没有增加任何新能力**——行为与 M6.4 完全一致
（同样的工具、同样的上限、同样的错误语义），只是运行时换了实现。

## 2. State 是什么

State 是节点之间传递的数据（`AgentState`）：

```python
@dataclass
class AgentState:
    messages: tuple[ChatMessage, ...] = ()
    tool_round: int = 0
    status: str = "running"        # running | completed | failed
    final_message: str | None = None
    failure: str | None = None
```

每个节点返回**部分更新**（dict），LangGraph 把它合并回 State：

```text
初始        : messages=[system, user], tool_round=0
agent_node  : messages += assistant(tool_calls)
tool_node   : messages += tool(result), tool_round=1
agent_node  : messages += assistant(final), status=completed
```

## 3. Node 是什么

Node 是只做一件事的函数：

- `agent_node`：State → `ModelProvider.chat()` → State 更新。**不调用工具、
  不接触 Platform、无 subprocess/docker/filesystem。**
- `tool_node`：State → `ToolGateway.execute()` → State 更新。**不接触
  PlatformClient/HTTP。**

## 4. Edge 是什么

Edge 是固定跳转：

```python
graph.add_edge(START, "agent")   # 入口
graph.add_edge("tools", "agent") # 工具执行后必然回到模型
```

## 5. Conditional Edge 是什么

条件边是唯一的"分岔点"：

```python
def should_continue(state) -> Literal["tools", "end"]:
    if state.status != "running": return "end"
    last = state.messages[-1]
    if last.role == "assistant" and last.tool_calls: return "tools"
    return "end"

graph.add_conditional_edges("agent", should_continue,
                            {"tools": "tools", "end": END})
```

路由逻辑只存在这一处（不散落）。轮次上限也在 `agent_node` 顶部做硬检查：
`tool_round >= MAX_TOOL_ROUNDS` 时直接 `status=failed`，不再发起模型调用——
与 M6.4 的计数完全一致（3 轮 = 3 次 LLM + 3 次工具，然后失败）。

## 6. Graph 是什么

Graph = State + Nodes + Edges，`compile()` 后得到可执行对象：

```python
builder = StateGraph(AgentState)
builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
builder.add_edge("tools", "agent")
compiled = builder.compile()

result = compiled.invoke(AgentState(messages=(system, user)))
```

没有使用 `create_react_agent` / `create_agent` / 任何 prebuilt agent——
节点、条件、循环全部自己定义（M6.5 的学习目标）。

## 7. LangGraph 与 ToolGateway 的关系

```text
LangGraph        = Agent orchestration（控制流）
ToolGateway      = Security / execution boundary（能力边界）
```

`tool_node` 只认识 `ToolGateway` Protocol：allowlist 注册表、JSON Schema
参数校验、未知工具拒绝、结构化错误、32 KiB 结果截断全部原样保留。
LangGraph 不会绕过 Gateway 直接执行 Python 函数。

## 8. LangGraph 与 Platform 的关系

```text
LangGraph → ToolGateway → ToolRegistry → Platform Tools → PlatformClient → Platform API
```

而不是 `LangGraph → EDA`。Graph 只负责状态转移；QA/Flow/EDA 判断仍然只在
Platform Backend。运行时通过 `AEMP_AGENT_RUNTIME=llm` 选择
`LangGraphAgentRuntime`（`LLMAgentRuntime` 保留为兼容子类）；
`AEMP_AGENT_RUNTIME=stub` 仍走 `StubAgentRuntime`。

## 9. 测试与边界（As-Built）

| 测试 | 内容 |
|---|---|
| `test_langgraph_runtime.py`（15） | 拓扑断言（START→agent、agent→tools/END、tools→agent、无 tools→tools/END→agent）、State 序列、Case1–8、Provider/无 Gateway 守卫 |
| `test_agent_loop.py`（11） | M6.4 行为回归（经兼容名 `LLMAgentRuntime` = 同一 Graph 实现） |
| `test_isolation.py`（7） | `langgraph_runtime.py` 不含 PlatformClient；pyproject 只允许 langgraph（禁 langchain/openai/anthropic/mcp/deepagents 直接依赖） |
| `test_tool_calling_http.py`（集成） | Agent HTTP → LangGraph → Tool → 真实 Platform HTTP → 404 → Tool Result → LLM → Final |

隔离纪律不变：Agent 无 shell/subprocess/docker/sqlite/文件系统权限；LLM 路径
仍只经 ToolGateway；Agent API Contract 与 Platform API Contract 均未修改。
