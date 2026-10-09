# AI EDA Methodology & QA Platform —— 开源选型分析与总体架构

> **Phase 1 交付物**：代码库分析 + 架构设计（不含实现代码）
> 日期：2026-10-07 ｜ 状态：待评审
> 分析方法：三路并行子代理克隆实读三个仓库全部源码（结论均带文件路径级证据），工具链许可证独立核验。

---

## 0. 执行摘要

| 项目 | 实际形态 | 许可证 | 对本案价值 |
|---|---|---|---|
| **Auto-EDA** | Claude Code 插件形态的 agent 方法论包：~2800 行，主体是 Markdown（7 个 agent + 8 个 SKILL.md + 模板），可执行代码仅 3 个小脚本 | 自定义 `LicenseRef-Auto-EDA-Attribution`（BSD-3 + 强制署名/引用，**非 OSI**） | ★★★★ 知识层/治理层样板：SKILL.md、symptom→fix 表、Gates 门禁、诚实测量原则 |
| **OpenADA** | 工程化 Agent-EDA contract + driver：Python 1.66 万行 + 495 个 JSON Schema/profile，105 个测试文件，CI 完整 | **MIT** | ★★★★★ 合同/审计层可近乎直接移植：双状态 result envelope、版本化 schema、SHA-256 lineage、run 溯源清单 |
| **eda-report-parser** | 620 行个人作品集小工具（单日完成，0 star，无 CI） | **无许可证（保留所有权利）** | ★★ 只可参考思想：零依赖解析、格式嗅探、真实报告 fixture 测试范式；**代码一行都不能复制** |

**核心架构决策**：确定性内核 + 边缘薄 Agent（§3）。MVP 阶段不含 LLM——先把 "flow spec → 工具执行 → 报告解析 → 规则 QA → run 历史" 做成可测试流水线；Agent/RAG 是 Phase 1 末尾的可选增量和 Phase 2 主战场。**只设计一个 Agent**（单 orchestrator），复杂度放在 skill 知识库而非 agent 数量上。

**许可证红线**（§2）：OpenADA（MIT）代码可复用（保留版权声明）；Auto-EDA 禁止文本级复制（思想无碍）；eda-report-parser 禁止复制任何代码（含 31 行基类）。

---

## 1. 三个开源项目深度分析

### 1.1 Auto-EDA（A 项）

**元信息**：commit `60b88c9`（2026-08-23，一次性发布），1 star / 0 fork，无 CI、无测试、无依赖声明。39 个文件约 2800 行，主体 Markdown。工具链：iverilog + Yosys + OpenROAD（ORFS）+ KLayout，NanGate45/FreePDK45，Apptainer 容器 pin 版本。

**结构**：`.claude-plugin/plugin.json` 注册 `agents/`（7 个 Markdown agent 角色，YAML frontmatter 声明 tools/model）与 `skills/`（8 个 `SKILL.md`）；`templates/`（config.mk.tmpl、SDC、shell 脚本模板，copy-and-fill 占位符风格）；`scripts/`（verify_obs_treaty.py、gen_macro_placement.py、area_report.py 共约 330 行）；`docs/`（7 篇编号方法论文档）。

**关键机制**：

1. **Agent**：无自研 LLM 调用代码，完全依赖 Claude Code harness。核心是 `agents/eda-orchestrator.md`——把 standing plan（INTAKE→VERIFY→HARDEN→ASSEMBLE→…→FREEZE）、G1~G5 门禁、"诚实原则" 全部写进 system prompt；orchestrator 通过 Agent tool spawn 专家 agent，每个专家定义了**结构化返回契约**（stage/DRC/WNS/log path）。
2. **Skills**：固定结构 Goal → Prerequisite → Procedure（精确 bash 命令）→ Traps → Output；skill 间显式声明依赖顺序；零代码注册。
3. **Symptom→fix 表**（`docs/03_HARD_RULES.md`）：把真实工具报错码（`GRT-0118`、`PDN-0006`、`RSZ-0060`）映射到根因与具体修复参数，CONTRIBUTING 规定"每次踩坑必须回填"——一个自改进的诊断知识库。
4. **Gates 机制**：廉价断言（die 放得下、OBS 层条约干净）自动执行，昂贵/难判定的决策停给人类；autonomous 模式下"不跳过 gate，而是应用编码规则并记录决策日志"。
5. **Fail-fast**：`gen_macro_placement.py` 在放置阶段就 assert 宏数量与 die 尺寸，把"3 小时 detailed route 后才爆炸"的错误变成秒级失败。
6. **Report Parser**：仅 `scripts/area_report.py`（69 行）——纯 regex 抓 Yosys area 与 OpenROAD log 的 WNS/TNS/DRC，输出平铺 dict，无抽象层、无测试，自己都承认脆弱；无 OpenSTA 专门 parser。

**值得借鉴**（按价值排序）：① SKILL.md 承载 EDA procedure 的组织法（天然跨 harness，对人也是 runbook）；② symptom→fix 表作为项目记忆 + 回填机制；③ Gates 三分法（cheap assert 自动 / expensive review 人工 / 决策必留痕）；④ 错误前移的 fail-fast 断言；⑤ 结构化返回契约 + 并行 fan-out；⑥ 并行 bracket 取代串行调参；⑦ 诚实测量原则（measured/scaled 分离、never silently improve a number）；⑧ frozen-config + 一键重建 + 容器 pin；⑨ 硬规则写在模板注释里（规则出现在使用现场）。

**不适合借鉴**：无引擎/无状态管理/零测试零 CI；regex 抓 log 的方式（应优先结构化输出）；强绑定 Claude Code 专有词汇；深度绑定 ORFS/45nm 的具体规则；文档与内容不一致（声称的 ORFS patch 不在仓库中）；单人一次性发布、效果不可独立核实。

### 1.2 OpenADA（B 项）

**元信息**：commit `ff9415e`（2026-08-12），280 commits（约 1 个月历史），Python 211 文件 + JSON 495 个；`src/openada` 1.66 万行、测试 4.7 万行；CI 双 workflow + 105 个 pytest 文件；运行时唯一依赖 `jsonschema>=4.18`。定位："agent harness 与 EDA 工具之间的开源语义边界"——**模拟/混合信号为主**（ngspice/Xyce/KLayout/Netgen），数字侧只有 Yosys lint/综合、Verilator lint、OpenSTA 单角 timing；OpenROAD 仅 discovered、无 structured operation。

**关键机制**：

1. **Agent-EDA Contract = 四层组合**：① 协议文档 `docs/CONTRACT.md`（driver 八条职责：解析可执行文件、launch 前校验输入、无 shell 的确定性 argv、限定运行时长与捕获量、**进程状态与工程状态分离**、归一化最小工程事实、记录输入输出 path/size/sha256、保留复现所需 provenance）；② 机器可读 JSON Schema（draft 2020-12，`additionalProperties: false` 封闭外层 + 开放 `data` 内层）；③ **不可变版本化 operation profile**（16 个，语义变更必须发新版本号，旧版永久保留）；④ `src/openada/contract.py` envelope 构造 + jsonschema 校验。
2. **Result envelope**（`schemas/result-v0alpha1.schema.json`）核心字段：`schema / operation / tool{name,path,version} / execution{status,exit_code,duration_ms,command,cwd} / engineering{status,summary} / inputs[] / artifacts[] / diagnostics[] / data / provenance`，其中文件项为 `{kind, role, path, exists, bytes, sha256}`。**最重要语义**：`execution.status: completed` ≠ `engineering.status: pass`；不可信/缺失证据是 `unknown` 绝不是 `fail`；文本全部 bounded（4000 字符保头尾）。
3. **Driver 层**：纯 subprocess（argv 无 shell），无统一基类，九个 driver 类（ngspice/xyce/klayout/netgen/yosys/verilator/icarus/xschem/opensta）靠 `driver_registry.py` 的 `BuiltinDriver` dataclass（alias/driver_id/version/native_tool/operation_profile/features/factory）注册。`process.py::run_process()`：双线程 drain、内存只留 tail（12k 字符）+ 总字节数/截断标志、16 MiB 硬上限、超时 `killpg`。**TOCTOU launch guard**：入口复验输入 SHA-256，防 resolve→launch 窗口被换文件；**闭包环境策略**（`closed-opensta-runtime-v1` 等）：不继承父 env、白名单注入。
4. **Evidence**：native 工件（raw/log/report）+ 归一化 typed facts；`stable_regular_file()` 用 O_NOFOLLOW + fstat 前后身份比对防替换、捕获同时算 SHA-256；跨操作靠 **artifact lineage** 串联（`{operation, request_id, artifact_role, artifact_sha256, binding: verified|unverified}`）；conformance 证据树带 **tamper 负例集**（篡改单字节/改测量值/换 digest 等十余种，独立 verify.py 必须全部拒绝）；Ed25519 scheduler seal 补足"SHA-256 不能阻止 agent 凭空编造"的缺口。
5. **可复现性**：conformance `run.json` 是范本——pinned 容器镜像 digest、design revision、**checkout 前后 git 状态哈希**（`tracked_files_modified: false`、`status_sha256`）、`network: "none during EDA execution"`；launch 前/后输入对账（`inputs_stable/changed_inputs`）；工具版本来自 bounded version probe（精确 grammar 匹配、绝不"推断版本"）。
6. **Scoped preflight**：agent 只声明一个固定 assertion，preflight 只探测一个工具、只推荐唯一下一步，明文"不遍历项目、不猜 PDK、`assertion_evaluated` 恒为 false"——把 LLM 的不确定性关在 contract 外。
7. **一段高价值注释**（envelope 强制落盘的原因）：envelope 曾只上 stdout，agent 被迫自己解析 raw 文件，"被选了四次，每次那个数都以 'measured' 名义上报而 result contract 无法为其背书"——**强制落盘结构化 envelope，使 typed chain 成为最短路径**。

**适合本案借鉴**（按价值排序）：① execution/engineering 双状态 + unknown 语义；② 封闭 envelope + 开放 data 的版本化 JSON Schema；③ 全链路 SHA-256 artifact lineage；④ conformance-as-release-gate（对外宣称的 feature 必须有 pinned run + 独立 verify + tamper 负例）；⑤ bounded capture 与进程治理；⑥ launch 前/后输入对账；⑦ scoped preflight；⑧ run.json 级溯源清单；⑨ closed typed measurement kernel（确定性内核 + digest 绑定）；⑩ skill 分层纪律（skill 不得重定义 assertion、不得把 log 启发式升格为 contract）。

**不适合借鉴**：模拟域 driver 代码（领域错位）；无统一 driver 基类（SDK 化程度低）；防御性过重（cli.py 3622 行、experiment.py 4958 行，逐路径校验 symlink/inode——采纳其不变量而非代码）；无数据库层；POSIX-only；大量 "planned/proposed" 内容需与 implemented 边界区分；项目极年轻（1 个月、单一公司、schema 可能变化）——**更适合移植设计而非 fork 依赖**。

### 1.3 eda-report-parser（C 项）

**元信息**：6 commits / 1 人 / 单日完成（2026-05-17），11 个 .py 共 620 行，0 star，无 CI；4 个 unittest（本地实跑通过）。零第三方依赖（仅 stdlib `re/csv/argparse/logging/abc`）。

**Parser 抽象**：`parsers/base_parser.py`（31 行）——ABC 基类，`read_file()` 读行 + 抽象 `parse() -> List[Dict[str, Any]]`，结果累积在 `self.data` 实例状态（**不可重入**：二次调用结果翻倍）。无注册机制、无工厂，`main.py` 硬编码 import 分支。每个 parser 类内私有 `_detect_format()` 做**格式嗅探**（Timing 扫前 50 行、DRC 扫前 30 行），一个类承接多种工具格式——这是该仓库最值得注意的模式。

**支持矩阵**：OpenSTA `report_checks -format full` 与通用 PT 风格（timing）；OpenROAD TritonRoute `-output_drc` 与自造 Simple 格式（DRC）。**明确不支持** PrimeTime/Innovus/ICC2/Tempus 专有格式，无 power/area/congestion/LVS。

**Timing 数据结构**（`parsers/timing_parser.py:67`，普通 dict）：仅 `Format / Startpoint / Endpoint / Path Group / Status(MET|VIOLATED) / Slack` 6 字段——path delay、arrival/required time、transition、capacitance、cell delay 全部丢弃；`Startpoint` 只取第一个 token，括号内 cell 信息丢失。

**DRC 数据结构**（`parsers/drc_parser.py:127`）：`Format / Rule / Layer / X1,Y1,X2,Y2 / Sources`——`Rule` 实为粗粒度 violation type（Short/Spacing/Enclosure/Width）而非 rule name；每个 violation 常有两行不同 layer 的 bbox，**只保留第一行**；Simple 分支 `X2/Y2` 用空字符串占位而 OpenROAD 用 float（**同字段双类型混用**）；无 severity、无 violating/required value；`total_violations` 读出后从未对账，是死变量（漏抓无任何提示）。

**输出**：`List[Dict]` → 仅 CSV（headers 自适应取 `data[0].keys()`）；无 JSON/DB。附带 `utils/reporter.py` 终端 ASCII 统计（WNS/TNS/分组计数），解析/导出/展示三层分离。

**值得借鉴**：① 零依赖、逐行 regex 解析对 GB 级日志内存友好，可作 parser 层工程基线；② "一个 Parser 类 + 格式嗅探"抗格式碎片化（需升级为显式注册机制）；③ 解析/导出/统计三层分离；④ **真实工具报告 fixture + mock `read_file` 单测范式**（先存样例报告 → 写 regex → 锁行为）；⑤ 自适应 CSV headers 小技巧。

**反面教材**（本案必须反向做对）：无 schema/弱类型（应使用 dataclass/TypedDict + 校验）；无对账无告警（解析条数必须与工具自报总数核对，不一致 → unknown）；`except Exception` 吞错；实例状态不可重入；解析深度远低于平台要求。

---

## 2. 许可证审计与第三方引入策略（F 项）

### 2.1 参考项目许可证

| 来源 | LICENSE | 定性 | 对本案约束 |
|---|---|---|---|
| Auto-EDA | 自定义 `LicenseRef-Auto-EDA-Attribution`（© 2026 Amir Ghazizadeh）：BSD-3 实体 + 强制署名 + 学术论文强制引用；GitHub 显示 NOASSERTION | source-available，**非 OSI 开源** | **禁止文本级复制**（含 SKILL.md/README 改写为 substantial portion 会触发署名义务）；设计思想、规则、流程不受版权保护，可放心借鉴。结论：只吸收方法论，全部内容重写，不做任何代码/文本搬运 |
| OpenADA | 标准 **MIT**（© 2026 Simra Tech），pyproject 声明 `license = "MIT"` | OSI 开源 | 代码可复用/修改/再分发，须保留版权与许可声明。仓库内第三方材料：IHP PDK 夹具随附 Apache-2.0，合规 |
| eda-report-parser | **无 LICENSE 文件**（GitHub API `license: null`；最后一次 commit 恰是"从 README 删除 License 段"，顶部的 MIT badge 是残留的虚假声明） | 默认保留所有权利 | **禁止复制、修改、再分发其任何代码**（包括 31 行 `base_parser.py`）。合法做法：借鉴思想与文件格式（思想/格式不受版权保护），独立实现；或联系作者授权。**clean-room 纪律：实现者不得边看其源码边写我们的实现** |

### 2.2 工具链自身许可证（本次独立核验，2026-10-07）

| 工具 | 许可证 | 说明 |
|---|---|---|
| Yosys | **ISC** | GPL 兼容，无风险 |
| OpenROAD | **BSD-3-Clause** | 无风险 |
| OpenSTA | **GPL-3.0**（Parallax 提供商业双许可） | GPL，见下述调用策略 |
| KLayout | **GPL-3.0**（GitHub 当前标注；以仓库 LICENSE 为准） | GPL，见下述调用策略 |
| OpenROAD-Flow-Scripts | BSD-3-Clause | 可参考其平台配置 |
| sky130 PDK | Apache-2.0 | 开源可用 |

**GPL 调用策略**：本平台以**独立子进程**调用 GPL 工具并解析其输出文件/JSON，不静态/动态链接其库、不复制其源码进本仓库、不与 GPL 工具组成单一分发作品——属经典 *mere aggregation*，GPL 不传染平台代码。约束：平台分发时**不捆绑 GPL 二进制**（用户/镜像自行安装），`doctor` 命令负责探测与版本记录；若未来做平台镜像分发，容器层公开构建脚本、GPL 工具的源码获取方式写入文档。

### 2.3 第三方代码引入策略（制度化）

1. **许可证白名单**：MIT / Apache-2.0 / BSD-* / ISC 可引入；GPL/LGPL 只允许"独立进程调用"形态进入；**无许可证 = 零代码复制**；自定义/非标许可证默认拒绝。
2. **Vendored 代码只进 `third_party/`**：原样保留（含原 LICENSE 文件与版权头），登记进 `third_party/NOTICE.md`（来源 URL、commit、许可证、引入原因、引入人/日期）；不做修改；确需修改时以独立 patch 文件记录并注明。
3. **pip 依赖**：走白名单审批，锁文件（uv/pip-compile）入库，形成事实 SBOM；runtime 依赖目标 ≤ 4 个。
4. **Clean-room 规则**：对无证代码（eda-report-parser 类），允许分析行为与文件格式样例，禁止"读着它的代码写我们的代码"；参与实现的 agent prompt 中明确排除。
5. **LLM 生成代码的来源审查**：code review 检查单加一条"是否疑似搬运了 GPL/无证代码片段"，高风险片段（regex、表驱动规则）重点核对。
6. **许可证复查时机**：引入时、上游发新版时、每次对外发布前。

---

## 3. 本项目总体架构（D 项 / G1 总体架构）

### 3.1 设计原则（对应"可运行、可测试、可解释、可扩展"）

| 原则 | 含义 | 来源 |
|---|---|---|
| P1 确定性内核优先 | LLM 不在确定性路径上；MVP 甚至没有 LLM 也能跑完全流程 | OpenADA（contract 外置 LLM） |
| P2 只有一个 Agent | 单 orchestrator + Markdown skill 知识库；不搞 agent 动物园 | 用户约束 + Auto-EDA 形态 |
| P3 双状态分离 | "进程跑完" ≠ "工程通过"；缺证据 = `unknown`，永远不把执行失败当作 QA fail，也不当 pass | OpenADA |
| P4 可追溯 | 任何 QA 结论 3 步内回溯：finding → rule → metric → artifact(sha256) → run → tool version | OpenADA lineage |
| P5 结构化优先 | 优先消费工具结构化输出（OpenROAD `-metrics` JSON、Yosys JSON），文本 regex 为降级路径，且**必须对账** | 三仓共同教训 |
| P6 依赖极简 | runtime：`pyyaml` + `jsonschema`（+CLI 库可选）；parser 子系统零第三方依赖 | eda-report-parser、OpenADA |
| P7 文件是事实，SQLite 是索引 | run 目录为 source of truth，数据库可随时由 run 目录重建 | OpenADA（无 DB 的教训反推） |
| P8 规则即数据 | QA 规则用 YAML 声明 + 少量内置校验函数注册表；不发明 DSL，不引入规则引擎框架 | — |
| P9 fail-fast 前移 | 每 step 前廉价断言（输入存在、工具版本、die 可容纳），昂贵错误秒级暴露 | Auto-EDA |
| P10 诚实测量 | 平台代码无权修改 metric；measured 与 estimated/scaled 永远分列 | Auto-EDA honesty doctrine |

### 3.2 分层架构

```
┌────────────────────────────────────────────────────────────────┐
│ L5  Agent 层（薄，Phase 1 末可选 / Phase 2 主战场）                │
│     单 orchestrator：自然语言 ↔ 调用 L4/L2 的 CLI 服务接口          │
│     + RAG（方法论文档/symptom→fix 知识库）                        │
│     纪律：agent 只能通过公开 CLI/服务接口行动，请求与响应全部留痕      │
├────────────────────────────────────────────────────────────────┤
│ L4  QA 与诊断层                                                  │
│     QA Rule Engine（YAML 规则包 + 内置校验函数注册表）              │
│     Failure Diagnosis（错误码/指标签名 → 根因，Phase 2 深化）       │
│     IP Release QA（发布检查清单 + lineage 生成 release notes）     │
├────────────────────────────────────────────────────────────────┤
│ L3  解析层                                                       │
│     Report Parser：注册表 + BaseParser + 格式嗅探                  │
│     OpenSTA timing / OpenROAD DRC&metrics.json / Yosys stat /    │
│     KLayout LYRDB（Phase 2）；输出 typed metrics/violations        │
├────────────────────────────────────────────────────────────────┤
│ L2  Flow 执行层                                                  │
│     Flow Engine：YAML Flow Spec → 线性步骤（Phase 2 支持 DAG/并行） │
│     Tool Drivers：yosys / openroad / opensta / klayout           │
│     （subprocess、无 shell argv、bounded capture、版本探针、        │
│       闭包环境、TOCTOU 输入复验）                                  │
│     Run Manager：run 目录、run.json 溯源、断点信息                 │
├────────────────────────────────────────────────────────────────┤
│ L1  基础设施层                                                    │
│     Schema：aemp.result/v0 envelope（JSON Schema 校验）           │
│     Storage：SQLite 索引（runs/steps/artifacts/metrics/           │
│              violations/qa_findings）+ run 目录文件系统            │
│     Config / Logging / Errors / CLI（doctor/run/parse/qa/report） │
└────────────────────────────────────────────────────────────────┘
```

### 3.3 关键架构决策（ADR 摘要）

| # | 决策 | 理由 |
|---|---|---|
| AD1 | MVP 无 LLM；Agent 是 CLI 之上的可选增量 | 可运行/可测试优先；确定性内核先行，agent 稳定性问题不阻塞平台 |
| AD2 | 自定义 `aemp.result/v0` envelope，语义移植自 OpenADA（双状态、bounded、sha256），schema 用 JSON Schema 校验 | OpenADA 的 schema 字段级合理且经受 105 个测试文件检验；但它是其项目身份的一部分，自建保持独立演进自由 |
| AD3 | Driver 不设重型抽象基类：共享进程治理 + envelope 工具函数，每工具一个轻类 + 注册表 | OpenADA 无基类的实践 + 避免过早抽象；等第 5 个 driver 出现再抽 |
| AD4 | 优先 `-metrics` JSON / 工具原生结构化输出；regex 解析器必须带对账（解析条数 vs 工具自报总数，不一致 → unknown + diagnostics） | Auto-EDA/erp 的 regex-on-log 脆弱教训 + erp 的对账缺失反面教材 |
| AD5 | Flow Spec 用 YAML、线性步骤；Tcl 模板进版本库由引擎确定性渲染 | 可解释：用户能读整个 flow；Phase 2 再加 DAG/并行 bracket |
| AD6 | Run 目录为事实，SQLite 仅索引，提供 `aemp rebuild-db` | 长期可审计；数据库损坏可恢复；Postgres 迁移只动 L1 |
| AD7 | Skill 用 Markdown 承载（Auto-EDA 组织法）但与 contract 严格分离（OpenADA 纪律：skill 不得重定义断言） | 知识可版本化、可人读、可被 RAG 索引，且不会污染数据契约 |
| AD8 | MVP 驱动工具直连（自己的 Tcl 模板），ORFS 作为 Phase 2 可选 backend | 避免 Makefile 间接层损害可解释性；ORFS 平台配置作为 PDK 参考来源 |

### 3.4 模块依赖图（G2）

**分层视图**（依赖只能向下/向内，禁止反向；agent 唯一入口是公开服务接口）：

```
                       ┌─────────────────┐
                       │  agent (L5, 薄) │
                       └────────┬────────┘
                                │ 仅调用公开 CLI/服务接口
                                ▼
   ┌───────────────────────── cli.py ─────────────────────────┐
   │ doctor / run / parse / qa / report / history / ask       │
   └───────┬──────────────┬───────────────┬──────────────┬────┘
           ▼              ▼               ▼              ▼
    ┌────────────┐  ┌──────────┐   ┌───────────┐  ┌───────────┐
    │ flow.engine│  │ qa.engine│   │  parsers  │  │ runs.cli  │
    │ spec→steps │  │ rules +  │   │ registry+ │  │ (history) │
    └──┬──────┬──┘  │ diagnosis│   │ base+嗅探  │  └─────┬─────┘
       │      │     └────▲─────┘   └─────▲─────┘        │
       ▼      │          │               │              ▼
  ┌────────┐  │          │        ┌──────┴───────┐ ┌───────────┐
  │drivers │  │          │        │ parsed typed │ │ runs.mgr  │
  │yosys   │─ ┘          │        │ metrics /    │ │ run_dir + │
  │openroad│──────────────┘        │ violations   │ │ run.json  │
  │opensta │  (解析产物喂给 QA)     └──────▲───────┘ │ SQLite 索引│
  │klayout │                               │         └─────┬─────┘
  └───┬────┘        artifacts (logs/reports/metrics.json)│
      ▼                                                  ▼
 yosys/openroad/opensta/klayout 二进制            core(schema/config/log/err)
```

**文字版依赖规则**（评审用，优先级高于图形）：

- `core`：无内部依赖（schema、config、error、logging、process 治理、hash 工具）
- `drivers` → `core`
- `parsers` → `core`（**不**依赖 drivers/flow，解析器必须是纯函数）
- `flow.engine` → `drivers`、`runs`、`core`
- `qa` → `parsers`（读 typed 结果）、`runs`、`core`（**不**依赖 drivers）
- `runs` → `core`
- `cli` → 以上全部
- `agent` → **仅** `cli` 公开命令（subprocess 或服务函数），不 import 内部模块——这是把 LLM 关在 contract 外的物理保证

### 3.5 核心数据模型

**`aemp.result/v0` envelope**（每个 step 一份，落盘于 run 目录并索引进 SQLite；字段语义移植 OpenADA，schema 自有）：

```
schema:      "aemp.result/v0"                # 封闭外层，additionalProperties: false
run_id / step_id: string
operation:   "flow.step/<name>/v0"
tool:        {name, version, path, argv[]}   # 版本来自探针，绝不推断
execution:   {status: completed|failed|timeout|invalid_request,
              exit_code, duration_ms, command, cwd,
              log_path, log_bytes, log_truncated}
engineering: {status: pass|fail|unknown|not_applicable, summary<=4000}
inputs:      [{role, path, exists, bytes, sha256}]
artifacts:   [{role, path, exists, bytes, sha256}]
metrics:     {...}                           # 开放区：由各 step 的 profile 封闭定义
diagnostics: [{severity: info|warning|error, code, message<=4000, hint?}]
provenance:  {aemp_version, created_at, host:{system,machine,python}, env_policy}
```

**run 目录布局**：

```
runs/<UTC时间戳>-<短哈希>/
├── run.json            # 溯源清单：flow spec 副本+sha256、设计源文件 sha256、
│                       # PDK/platform、各工具版本、git commit+dirty、host、network policy
├── flow.spec.frozen.yaml
├── logs/               # 每步完整原生日志（原样保留）
├── artifacts/          # 报告/metrics.json/netlist 等
├── results/            # 每步 envelope：step-01-synth.result.json …
└── qa/                 # findings.json + run report.md
```

**SQLite 表**（索引，均可由 run 目录重建）：`runs`、`steps`、`artifacts`、`metrics`（长表：run_id/step_id/name/value/unit/source_artifact_id，便于跨 run 聚合）、`violations`（kind/rule/layer/coords/severity/source_artifact_id）、`qa_findings`（rule_id/rule_version/severity/observed/threshold/message/evidence_artifact_id）。

**QA 规则包示例**（示意，非实现）：

```yaml
# configs/rules/base_timing.yaml
pack: base_timing
version: 1
rules:
  - {id: timing.wns.non_negative,  metric: sta.wns_ns,                check: ge,       threshold: 0.0,            severity: error}
  - {id: route.drc.zero,           metric: route.drc_violation_count, check: eq,       threshold: 0,              severity: error}
  - {id: area.utilization.window,  metric: place.utilization_pct,     check: between,  threshold: [40.0, 85.0],   severity: warning}
```

---

## 4. 推荐目录结构（G3）

在既有骨架上落地（包名建议 `aemp`，AI EDA Methodology Platform）：

```
ai-eda-methodology-agent/
├── AGENTS.md                  # 已建：补上 §2.3 引入策略与 §3.1 设计原则
├── README.md                  # 已建
├── pyproject.toml             # M0 建立；runtime 依赖：pyyaml、jsonschema（+CLI 库可选）
├── docs/                      # 已建：本文件；后续 ADR、方法论文档
├── configs/
│   ├── flows/                 # Flow Spec（YAML）——"EDA Flow Spec 管理"的载体
│   ├── rules/                 # QA 规则包（YAML）
│   └── tools/                 # 工具可执行路径/版本约束/闭包环境白名单
├── src/aemp/
│   ├── core/                  # config、error、logging、process 治理（bounded capture/
│   │                          #   版本探针/超时 killpg）、hash、schema 校验入口
│   ├── schema/                # aemp.result/v0 JSON Schema + envelope 构造
│   ├── drivers/               # base.py（轻契约）+ yosys.py / openroad.py / opensta.py
│   │                          #   / klayout.py + registry.py
│   ├── flow/                  # flow spec 模型、引擎（YAML→步骤）、Tcl 模板渲染
│   ├── parsers/               # base.py + registry.py + opensta_timing.py /
│   │                          #   openroad_drc.py / yosys_stat.py / openroad_metrics.py
│   ├── qa/                    # rule engine（内置 check 函数注册表）、内置规则包、
│   │                          #   diagnosis（Phase 2）、release_qa（Phase 2）
│   ├── runs/                  # run 目录管理、run.json、SQLite 索引、rebuild-db
│   ├── agent/                 # Phase 2：薄 orchestrator + RAG（保持一个模块，不膨胀）
│   └── cli.py
├── tests/
│   ├── unit/                  # 纯函数单测：schema 校验、每个 parser、每个 check 函数
│   ├── integration/           # 装有工具链时才跑的端到端（无工具则 skip）
│   └── fixtures/reports/      # ★ 真实工具输出样本（golden），按工具版本分目录
├── examples/
│   └── counter_nangate45/     # 端到端示例：最小设计 + 开源 PDK，一键可复现
├── scripts/                   # 环境探测、CI 辅助、golden fixture 采集脚本
├── data/                      # 仅小型合成样例（NDA/私有 PDK 一律不入库，.gitignore 兜底）
└── third_party/               # MIT/Apache 级 vendored 代码 + NOTICE.md（见 §2.3）
```

---

## 5. 复用策略（E 项 / G6 开源项目复用清单）

| 来源 | 内容 | 许可证 | 行动 |
|---|---|---|---|
| OpenADA | result envelope 字段集与双状态语义；driver 八条职责；run.json 溯源清单；bounded capture/TOCTOU/闭包环境的不变量；conformance-as-release-gate 思想 | MIT | **语义级复用（schema 字段、不变量清单照搬设计），代码级默认自写**。仅当某个通用工具函数（如 bounded capture）确有价值时 vendored 到 `third_party/` 并保留其 MIT 版权声明 |
| OpenADA | 模拟域 driver（ngspice/xyce 等）、防御性实现细节（逐路径 inode/symlink 校验） | MIT | **不复用**：领域错位、复杂度不合身 |
| Auto-EDA | SKILL.md 组织法、symptom→fix 知识库形式、Gates 机制、fail-fast 断言、诚实测量原则、模板注释承载硬规则 | 自定义署名许可 | **只参考思想，全部内容自写**；不在本仓库出现其文本；如未来论文引用该项目则按其 CITATION.cff 引用 |
| Auto-EDA | `area_report.py` regex 解析 | 同上 | **不复用**（且质量不达标，见 AD4） |
| eda-report-parser | 零依赖解析基线、格式嗅探模式、真实报告 fixture 测试范式、解析/导出/统计三层分离 | **无许可证** | **只参考思想，clean-room 重写**；其源码不作为任何实现时的参照文本 |
| eda-report-parser | 任何代码（含 31 行基类、regex、数据结构） | 无许可证 | **禁止复制** |
| ORFS | nangate45/sky130 平台配置、PDK 处理经验 | BSD-3 | Phase 1 作参考样例；Phase 2 可选作 flow backend |
| 工具链 | Yosys(ISC)/OpenROAD(BSD-3)/OpenSTA(GPL-3)/KLayout(GPL-3) | 见 §2.2 | 独立进程调用；不链接、不捆绑、不复制源码 |

**自研清单**（核心资产，无现成可搬）：Flow Spec 模型与引擎、深度报告解析器（完整 path 明细/全部 bbox/severity）、QA Rule Engine 与规则包、SQLite 历史与聚合、诊断知识库、Agent/RAG 层、CLI。

---

## 6. 第一阶段 MVP 范围（G4）

**目标**：一条命令跑通 "spec → yosys/openroad/opensta 执行 → 解析 → 规则 QA → run 历史"，全程无 LLM，任何结论可追溯。

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 工程基座**（~0.5 周） | pyproject、包骨架、config/logging/error、CI（lint+pytest，ubuntu） | `pip install -e .` 后 `pytest` 绿 |
| **M1 envelope + Run 管理**（~1 周） | `aemp.result/v0` JSON Schema、run 目录、run.json、SQLite 索引、`aemp history` | schema 校验有正负用例；run 目录可重建 DB |
| **M2 Drivers**（~1.5 周） | DriverBase 契约（无 shell argv、bounded capture、版本探针、超时、闭包环境、输入 sha256 复验）；yosys / opensta / openroad driver；klayout 占位；`aemp doctor` | 无工具时 doctor 明确报缺；有工具时版本入 run.json |
| **M3 Flow 引擎 v0**（~1 周） | YAML Flow Spec → 线性步骤执行；spec 冻结副本；step 前廉价断言 | `aemp run configs/flows/counter_nangate45.yaml` 一键端到端 |
| **M4 Parsers**（~1.5 周） | BaseParser+注册表+格式嗅探；openroad `-metrics` JSON（优先）；OpenSTA timing 完整字段（slack/path delay/arrival/required/transition/cap）；OpenROAD DRC（全部 bbox、rule name、severity）；yosys stat；对账检查 | 每个解析器有 golden fixtures + 正负用例；对账失败 → unknown + diagnostics |
| **M5 QA Rule Engine v0**（~1 周） | YAML 规则包加载、内置 check 函数注册表（ge/le/eq/between/absent…）、findings 落库、run report（md+json） | 每个 finding 可回溯 rule→metric→artifact→run；missing metric → unknown 不误判 |
| **M6 最小 Agent**（可选，~1 周） | 单 orchestrator 函数调用循环，仅能调用 CLI 子命令；全部请求/响应留痕；无 RAG | 砍掉不影响 MVP 验收；何时上由团队决定 |

**MVP 明确不做**：RAG、多 agent、DAG 流程/并行执行、Postgres、Web UI、商业工具（PT/ICC2/Tempus）解析、分布式执行、Windows 原生支持（目标平台 Linux；开发期用 WSL）。

**测试策略**：解析器=纯函数+golden fixtures（真实报告样本按工具版本分目录入库）；集成测试检测到本机无工具链自动 skip；CI（GitHub Actions, ubuntu）装 oss-cad-suite/OpenSTA 跑冒烟链。

**测试用 PDK/设计**：nangate45（首选，小而快）、sky130（贴近真实），均来自 ORFS 平台目录（BSD-3/Apache-2.0 系）；`data/` 只放合成小样例，私有 PDK 永不入库。

---

## 7. 第二阶段扩展范围（G5）

1. **Agent + RAG 主战场**：methodology Q&A（对 docs/ 与 symptom→fix 知识库建索引）、run 诊断助手（读 envelope/findings 给假设）、skill 库（Markdown，与 contract 分离）；模型适配层保持轻量（接口先行，不绑框架）。
2. **Failure Diagnosis 深化**：错误码/指标签名 → 根因假设 → 建议下一步；每次人工确认回填知识库（Auto-EDA 回填机制的正式化）。
3. **Sweep / bracket 并行参数搜索**：多 run 变体管理、QoR 对比、0-DRC 最小者保留策略。
4. **Library / PDK QA 深化**：Liberty 解析与一致性检查（corner 完整性、单位/阈值一致性）、SDC lint、LEF/DEF 基本检查。
5. **KLayout DRC/LVS 接入完整化**（LYRDB 解析进 parser 层）。
6. **IP Release QA**：发布检查清单引擎 + 基于 lineage 自动生成 release notes（哪些 run/工具/哈希支撑本次发布）。
7. **流程增强**：DAG 步骤与并行执行；ORFS 作为可选 backend。
8. **存储与界面**：Postgres 迁移（只动 L1）、Web dashboard（run 浏览/QoR 趋势）、MCP server 形态对外暴露。

---

## 8. 风险清单（G7）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **许可证合规**：Auto-EDA 非标许可、erp 无证、LLM 生成代码可能夹带 GPL 文本 | 法律风险 | §2.3 制度化（白名单、NOTICE、clean-room、review 检查单）；本仓库不出现三项目的文本/代码 |
| R2 | **工具版本漂移与报告格式变化**（跨 Yosys/OpenROAD/OpenSTA 版本输出措辞/字段变动） | 解析静默失效 | doctor 版本探针 + run.json 记录版本；fixtures 按版本 pin；结构化输出优先（AD4）；解析对账 + unknown 兜底 |
| R3 | **文本解析脆弱**（regex-on-log 教训） | QA 结论不可信 | 对账必做；解析层 100% 纯函数可测；失败产出 diagnostics 而非崩溃 |
| R4 | **LLM 幻觉污染测量值** | QA 平台公信力崩塌 | metrics 只能由 parser 从带 sha256 的 artifact 产生；agent 无写 metrics/DB 权限（§3.4 依赖规则物理保证）；诚实测量原则入 AGENTS.md |
| R5 | **工具链安装差异**（Linux 目标 vs 本地 Windows 开发） | 开发体验差、环境问题频发 | drivers 做平台断言并 fail-loud；开发用 WSL/容器；doctor 前置检查 |
| R6 | **NDA/私有 PDK 数据泄露** | 商业/法律风险 | 私有数据永不入库；`data/` 仅合成样例 + .gitignore 兜底；开源 PDK 走 ORFS 官方渠道 |
| R7 | **SQLite 并发/规模上限** | 历史查询变慢 | WAL + 单写者；metrics 长表 + 适度索引；存储接口化，Postgres 只动 L1 |
| R8 | **上游项目极年轻**（三仓均 <3 个月、低采纳、schema 可能变化） | 引入的语义/代码后续无人维护 | 只移植语义不 fork 依赖；vendored 代码限定少量通用函数；关注点在自己实现 |
| R9 | **范围蔓延**（9 大能力 + 三个参考项目诱惑） | MVP 迟迟不可运行 | §6 的"明确不做"清单冻结；新需求一律进 Phase 2 backlog |
| R10 | **Tcl 资产碎片化**（模板散落、不可追溯） | 流程不可复现 | Tcl 模板进版本库、由 flow 引擎确定性渲染、模板变更走 review |

---

## 附录：分析方法与证据

- 三仓库分析由三路并行子代理完成：克隆实读全部源码（Auto-EDA commit `60b88c9`；OpenADA commit `ff9415e`；eda-report-parser commit `b3fd340`），所有结论带文件路径与代码摘录级证据；仓库不存在风险已排除（均克隆成功）。
- 工具链许可证于 2026-10-07 通过各仓库 GitHub 页面独立核验（Yosys=ISC、OpenROAD=BSD-3-Clause、OpenSTA=GPL-3.0、KLayout=GPL-3.0）。
- 本文档为 Phase 1 交付物；下一阶段（M0）启动前建议评审 §3.3 架构决策与 §6 的 MVP 边界。
