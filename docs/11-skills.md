# Skill System（M7.1）

> 实现：`agent-backend/src/agent_backend/skills/`（运行时）
> + `agent-backend/skills/*/SKILL.md`（内容，数据）
> 相关：[docs/09-langgraph-runtime.md](09-langgraph-runtime.md)、[docs/10-hitl.md](10-hitl.md)、[docs/08-agent-backend.md](08-agent-backend.md)

## 1. Skill 是什么

Skill 是 **Knowledge + Procedure + Guidance** 的 Markdown 封装：告诉 Agent
"什么时候用这个能力、需要什么输入、按什么步骤处理、调用哪些 Tool、如何解释
结果、有哪些限制、缺失证据怎么办"。

```text
Skill = 领域知识 + 工作方法（不可执行）
Tool  = 可执行能力（经 ToolGateway 才可执行）
```

## 2. Skill vs Tool

| | Skill | Tool |
|---|---|---|
| 形态 | Markdown 数据（SKILL.md） | Python 可执行能力（tools/） |
| 作用 | 影响模型"怎么想/怎么用工具" | 真正执行动作 |
| 出口 | 只进入 LLM context | ToolGateway → PlatformClient → Platform API |
| 风险控制 | 不拥有任何执行权 | PolicyEngine + HITL |

Skill 绝不能变成 `Skill → subprocess` 或 `Skill Tool → 执行`；
Skill 只能是 `Skill Selection → Skill Context → Tool Calling → ToolGateway`。

## 3. Skill vs Prompt

- System Prompt 是**规则与身份**（代码常量，随版本管理）。
- Skill 是**按需加载的领域知识**（数据文件），以明确边界注入：

```text
<skill name="timing-analysis" version="1.0">
...SKILL.md 正文...
References (available on request, not loaded): sta-metrics.md
</skill>
```

Skill 内容不与 System Prompt 混成不可区分的一大段；总注入量有
`MAX_SKILL_CONTEXT_BYTES`（32 KiB）上限。

## 4. Skill vs Agent

Skill 不是 Agent：没有循环、没有工具调用权、没有独立 LLM 调用。
M7.1 只有一个 Agent（LangGraph runtime）；Skill 只是它的一次上下文装配。
以后 MCP/Skill 生态也不引入 Multi-Agent。

## 5. Progressive Disclosure

```text
Level 1  Skill Catalog（name/description/version/tags，恒在，~几行）
Level 2  SKILL.md（只加载命中的技能，最多 2 个）
Level 3  references/*（不自动加载；loader 按需读取，context 只提示文件名）
```

选择策略（M7.1，确定性、无 embedding/向量库）：
tag/description 关键词匹配（ASCII 词边界匹配，避免 "status" 命中 "sta"；
中文 tag 用子串匹配）；得分排序，最多 2 个；命中 0 个则只注入 Catalog。

## 6. Skill Registry

`SkillRegistry(root)` 启动时扫描配置的 Skill Root（默认
`agent-backend/skills`，可用 `AEMP_SKILLS_DIR` 覆盖）：

- 目录名必须等于 frontmatter `name`（`[a-z0-9][a-z0-9-]{0,63}`）；
- `description` / `version` 必填；重复 name 或非法元数据**启动即报错**；
- `list() / get(name) / exists(name)`。

## 7. Skill Loader

`SkillLoader(root)`：

- `load_skill(name)`：读 `SKILL.md`（≤32 KiB，超限报错）；
- `load_reference(name, relpath)`：只读该技能 `references/` 下的相对路径
  （≤64 KiB）；绝对路径（POSIX/Windows）、`..`、符号链接逃逸一律
  `SkillPathError`，**读取之前**拒绝；
- `reference_names(name)`：只列文件名，不读内容。

## 8. Skill 安全边界

Skill 内容属于 **untrusted / semi-trusted configuration**：

- 不执行 Markdown 中的代码，不动态 import，不 eval；
- Skill 运行时代码（`skills/*.py`）不含 subprocess/shell/Docker/SQLite/
  ToolRegistry/ToolGateway/PlatformClient（测试扫描强制）；
- Skill 不能新增/修改 Tool Registry，不能改变 PolicyEngine，不能绕过 HITL；
- 真正边界始终是 `Policy → ToolGateway → ToolRegistry → PlatformClient → Platform API`。

## 9. Skill 与 HITL 的关系

Skill 只影响 System Prompt；`run_flow` 仍是 HIGH risk：

```text
"Run RTL to STA"
  → flow-engineering / timing-analysis 注入（知识）
  → LLM 决定 tool_call run_flow
  → PolicyEngine（high）→ interrupt → 人工审批 → resume → 执行
```

Skill 不得影响 approval / reject / resume / thread 绑定。M6.6 测试全部保持通过
（含"技能已加载时 run_flow 仍然 interrupt"的专项测试）。

## 10. 当前四个 Skill

| Skill | 用途 | 关键内容 |
|---|---|---|
| `flow-engineering` | Flow/Run/Step/Artifact 与失败语义 | `run_flow` 是 HIGH risk；双状态解释；证据纪律 |
| `timing-analysis` | STA 结果解释 | 真实指标 `sta.wns_ns / sta.tns_ns / sta.path_count / sta.violation_count`；WNS/TNS 解读；判定归 QA |
| `library-qa` | Library/PDK QA 概念与未来方向 | 明确"未实现真实 PDK/Liberty 生产校验"，不虚构能力 |
| `failure-diagnosis` | 失败诊断方法论 | 证据顺序、失败分类、假设与下一步；明确无 RAG/Memory |

每个 Skill 至少一个 `references/*.md`（方法论资料，非可执行脚本）。

## 11. 为什么暂时不用 RAG

M7.1 的技能数量少、边界清晰，关键词匹配足以确定性选择，且**零新依赖**
（无向量库/embedding/Redis/PostgreSQL）。RAG 的价值在大量方法论文档与历史案例
（M7.3 Memory / 后续 RAG），现在引入只会增加不可解释性。

## 12. 下一阶段 MCP 如何接入（M7.2 已落地）

MCP 是 **Tool Transport**，不是 Agent Runtime、也不是 Skill 系统：

```text
Agent Runtime → Tool Gateway → { Local Tools, MCP Client → MCP Server }
```

M7.2 已按此实现：MCP Server 把 4 个契约能力以 Tool 形态暴露，Agent 侧
`MCPGateway` 仍走同一 `ToolGateway` 协议与同一 allowlist（仍经
PolicyEngine/HITL）；Skill 仍是 Markdown 知识层，未因 MCP 改动一行代码。
此外 Skill 数据同时以只读 Resource 暴露：

```text
methodology://skills/catalog
methodology://skills/{name}
methodology://skills/{name}/references/{path}
```

详见 [docs/12-mcp.md](12-mcp.md)。
