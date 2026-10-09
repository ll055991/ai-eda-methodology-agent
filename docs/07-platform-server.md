# Platform HTTP Server（M6.1）

> 实现：`src/aemp/api/http.py`（FastAPI Transport Adapter）
> 契约：[contracts/platform-api.yaml](../contracts/platform-api.yaml)（设计源）
> 相关：[docs/04-api-boundary.md](04-api-boundary.md)

## 1. 定位

```text
Agent Backend / curl / 任何 HTTP 客户端
        │  HTTP（Platform API Contract）
        ▼
Platform HTTP Server（FastAPI）        ← Transport Adapter，不是业务层
        │  只调用 PlatformApi
        ▼
PlatformApi → Flow / Run / Parser / QA / History
        ▼
ToolDriver.execute → Docker → EDA 工具
```

HTTP 层不做任何 Flow/Run/QA 逻辑，也不直接触碰 Driver、Docker、SQLite、
文件系统。`/openapi.json` 直接发布 `contracts/platform-api.yaml`（外部
`$ref` 内联），而不是从实现反向生成。

## 2. 启动

```bash
# 方式一（推荐）
python scripts/run_platform_server.py

# 方式二
uvicorn aemp.api.http:app

# 方式三
python -m aemp.api.http
```

环境变量（均有默认值，可覆盖）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AEMP_HOST` | `127.0.0.1` | 绑定地址（默认只监听本机） |
| `AEMP_PORT` | `8000` | 绑定端口 |
| `AEMP_LOG_LEVEL` | `INFO` | 日志级别（aemp + uvicorn） |
| `AEMP_CORS_ORIGINS` | 空（禁用） | 逗号分隔 allowlist；不默认放开 `*` |

依赖：`pip install -e ".[server]"`（fastapi + uvicorn；测试另需 `.[dev]` 的 httpx）。

## 3. Endpoints

| Method | Path | 说明 |
|---|---|---|
| GET | `/healthz` | 存活探针，恒 `{"status": "ok"}`；**不代表** EDA 环境就绪 |
| GET | `/docs` | Swagger UI（渲染契约文档） |
| GET | `/openapi.json` | 契约本体（platform-api.yaml，$ref 已内联） |
| POST | `/api/v1/flows/run` | 执行 flow spec（同步；inputs 覆盖暂不支持 → 400） |
| GET | `/api/v1/runs` | 历史分页（`limit`/`offset`/`status`，SQL 层过滤） |
| GET | `/api/v1/runs/{run_id}` | run detail（双状态 + metrics + artifacts + provenance） |
| POST | `/api/v1/runs/{run_id}/qa` | 规则包 QA（PASS/FAIL/UNKNOWN + findings） |
| GET | `/api/v1/runs/{run_id}/artifacts` | 已注册 artifact 列表（元数据） |
| GET | `/api/v1/runs/{run_id}/artifacts/{artifact_id}` | 单个 artifact 元数据 |

`/healthz` 只表示 HTTP Server 存活：Docker 不可用、Yosys/OpenSTA 缺失都不会
让它返回 500。EDA 环境就绪探针（`/readyz`）留待后续单独设计。

## 4. 错误语义

统一错误体（`contracts/schemas/error.json`）：

```json
{ "error": { "code": "RUN_NOT_FOUND", "message": "...", "details": {} } }
```

| code | HTTP | 触发 |
|---|---|---|
| INVALID_REQUEST | 400 | 请求结构/未知字段/非法参数（含 Pydantic 校验失败） |
| RUN_NOT_FOUND | 404 | run 不存在或 run_id 非法 |
| FLOW_NOT_FOUND | 404 | flow spec 不存在 |
| FLOW_VALIDATION_FAILED | 422 | spec 校验失败 |
| TOOL_ENVIRONMENT_UNAVAILABLE | 503 | Docker daemon / 镜像不可用 |
| TOOL_EXECUTION_FAILED | 500 | 驱动层意外失败 |
| QA_EVALUATION_FAILED | 500 | 规则包加载/评估失败 |
| ARTIFACT_NOT_FOUND | 404 | artifact 未注册 |
| INTERNAL_ERROR | 500 | 未预期异常（服务端记 traceback，响应只给固定文本） |

**失败的执行是 200**：`status=failed` 的 run 是被记录的工程事实，不是 HTTP 错误。
响应中永远不出现 traceback、本机路径、环境变量或 docker 命令。

## 5. 请求日志

每个请求记录 `request_id / method / path / status / duration_ms`（aemp logger）。
`X-Request-ID` 由客户端提供或服务端生成，并回写到响应头；不记录请求体、
Authorization 等敏感内容。

## 6. 同步限制与未来边界

- **M6.1 = synchronous**：长 flow 会占用 HTTP 连接直到执行结束；
  超时由既有 `ToolDriver`/`FlowEngine` 负责，HTTP 层不实现 EDA timeout。
- 异步 Job / SSE / WebSocket 属于后续 Agent Backend 里程碑，不引入
  Celery/Redis/新数据库。
- 认证（JWT/OAuth/RBAC）暂不实现；安全边界仍保持严格：
  `run_id` 格式先行校验、Artifact allowlist、无任意路径/命令入口。

## 7. Docker 要求

只有 `POST /api/v1/flows/run`（真实 EDA 执行）需要 Docker 与
`configs/tools/*.yaml` 中的 pinned 镜像；其余端点（history/run/qa/artifact）
不依赖 Docker。Docker 缺失时该端点返回 `503 TOOL_ENVIRONMENT_UNAVAILABLE`，
其余服务正常。
