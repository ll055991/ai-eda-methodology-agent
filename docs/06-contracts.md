# Contracts：Platform API 与 Agent API 契约（M6.0）

> 契约目录：[contracts/](../contracts/)（语言无关，独立于 Python 实现）
> 相关：[docs/04-api-boundary.md](04-api-boundary.md)

## 1. 目录与规范性

```text
contracts/
├── platform-api.yaml        # OpenAPI 3.1：Flow / Run / QA / Artifact / History
├── agent-api.yaml           # OpenAPI 3.1：Frontend ↔ Agent（chat + x-agent-tools）
└── schemas/
    ├── flow.json            # POST /flows/run 请求
    ├── run-summary.json     # POST /flows/run 响应
    ├── run.json             # GET /runs/{run_id} 响应
    ├── run-list.json        # GET /runs 响应
    ├── qa-request.json      # POST /runs/{run_id}/qa 请求
    ├── qa.json              # QA 响应
    ├── artifact.json        # 单个 artifact
    ├── artifact-list.json   # artifact 列表
    ├── error.json           # 统一错误体
    ├── agent-chat-request.json
    └── agent-chat-response.json
```

- **YAML 是描述，JSON Schema 是规范**：所有 DTO 校验走
  `aemp.api.schemas.validate_contract`（复用 M0 的 draft 2020-12 校验器）。
- 所有对象 `additionalProperties: false`（封闭 DTO）；未知字段必须被拒绝。
- 契约不引用 Python 类型；`src/aemp/api/` 只是契约的一个实现。

## 2. 对象模型（不是同一个东西）

```text
Run（一次受追踪的执行）
 ├── Step（flow 中的一步，step_id）
 │    └── ToolExecution（一次工具进程：argv/exit_code/duration/log）
 ├── Artifact（被注册的产物：role/path/bytes/sha256）
 ├── Metric（事实：<domain>.<metric>_<unit> → number，由 Parser 产生）
 ├── Diagnostic（info/warning/error 说明，不是判定）
 └── QA Finding（判定：rule_id + actual + expected + severity + status）
```

关系硬规则：

- Metric 与 Finding 永不同层：`run.json` 的 `metrics` 只有事实；判定只在
  `qa.json` / QA API 响应中。
- Step / ToolExecution / Artifact / Metric / Finding 各有独立身份，不得互相代替。
- 文件证据：`exists=true` 必带 `bytes+sha256`；`exists=false` 禁止携带哈希
  （不伪造指纹，M1 语义）。

## 3. 关键 DTO 语义

### Run（双状态）

```json
{
  "run_id": "20261007T154006Z-f96b16",
  "lifecycle_status": "success",
  "execution_status": "success",
  "engineering_status": "unknown",
  "metrics": { "sta.wns_ns": -0.01 },
  "artifacts": [ { "artifact_id": "artifacts/timing.rpt", "role": "sta.report",
                   "path": "artifacts/timing.rpt", "exists": true,
                   "bytes": 1234, "sha256": "…" } ]
}
```

- `execution_status`：工具进程是否跑完（success/failed/unknown）。
- `engineering_status`：工程判定（pass/fail/unknown；兼容 M1 旧词
  success/failed）。
- **执行成功 ≠ 工程合格**：OpenSTA exit 0 + WNS<0 ⇒ execution=success，
  engineering=fail。

### QA

```json
{
  "run_id": "…",
  "status": "FAIL",
  "rule_pack": { "name": "base_timing", "version": 1 },
  "summary": { "total_rules": 2, "passed": 0, "failed": 2, "unknown": 0, "warnings": 0 },
  "findings": [ { "rule_id": "timing.wns.non_negative", "metric": "sta.wns_ns",
                  "check": "ge", "threshold": 0.0, "severity": "error",
                  "status": "fail", "expected": ">= 0.0", "actual": -0.01,
                  "message": "…" } ]
}
```

- 整体 `status` 大写（PASS/FAIL/UNKNOWN，UI 门禁）；finding `status` 小写
  （pass/fail/unknown，与 M5 模型一致）。
- 整体优先级：FAIL（error-severity fail）> UNKNOWN > PASS；warning fail 可见但不 gate。
- 缺 metric ⇒ finding `unknown`，绝不 0/绝不 pass/绝不 fail。

### Error

见 [docs/04-api-boundary.md](04-api-boundary.md) §4（code → HTTP 映射）。
调用方只依赖 `code`。

## 4. Traceability（契约级保证）

每个 Agent 事实性结论都必须能沿以下链回到证据：

```text
User Request → Agent Tool Call → run_id → Step → Artifact(sha256)
             → Report → Metric → Rule(rule_id) → Finding
```

契约对追溯的支撑：

| 环节 | 契约字段 |
|---|---|
| Tool Call → Run | Agent 响应 `run_id`；Tool 只允许调用 Platform endpoint |
| Run → 产物 | `run.json` 的 `artifacts[]`（`artifact_id` + `sha256` + `bytes`） |
| 产物 → 事实 | `run.json` 的 `metrics`（`<domain>.<metric>_<unit>`） |
| 事实 → 判定 | `qa.json` 的 `findings[].rule_id/metric/actual/threshold` |
| 判定 → 规则版本 | run 内冻结的 `qa/rules.frozen.yaml` + `rule_pack.version` |

## 5. 版本与演进

- 契约路径带版本前缀（`/api/v1`）；破坏性变更 → 新前缀或新 schema 版本。
- **`aemp.result/v0` 不受 M6.0 影响**：QA 证据仍在 `qa/qa.json`，Run envelope
  仍是事实层；M6.0 未修改该 schema。
- 预留扩展字段（approval 三件套）只出现在 Agent Contract，且明确标注
  "reserved / not implemented"。
- Contract 变更流程：改 `contracts/` → 同步 `src/aemp/api/` DTO 映射 →
  contract tests 更新 → 文档同步（本目录）。
