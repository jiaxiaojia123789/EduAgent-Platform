# P2 — 工程化与生产可用性改进说明

本文档汇总 P2 批次引入的工程能力，供面试讲述与生产运维参考。

## 改进清单

| # | 改进点 | 落地位置 | 面试讲述点 |
|---|---|---|---|
| 9 | CI/CD 流水线 | `.github/workflows/ci.yml` + `backend/pyproject.toml` | ruff lint → pytest 覆盖率 → Docker 烟雾测试 → pip-audit 安全扫描 |
| 10 | Alembic 数据库迁移 | `backend/alembic.ini` + `backend/alembic/` + 初始迁移 `0001_init` | 替代 `init_db()` 自动建表，避免 schema 漂移；存量环境用 `alembic stamp` 标记基线 |
| 11 | OpenTelemetry 分布式追踪 | `backend/app/core/telemetry.py` + 自动埋点 | FastAPI/httpx/SQLAlchemy/Redis 全自动埋点 + LangGraph 节点 span，trace_id 同步到 structlog |
| 12 | structlog 结构化日志 | `backend/app/core/logging.py` + `app/middleware/request_context.py` | request_id 贯穿 HTTP → LangGraph → LLM 调用，所有日志可串联 |
| 13 | OpenAPI 文档定制 | `backend/app/main.py` + `app/schemas/errors.py` | 标签分组 / 统一 ErrorResponse / 401/403/500 错误码示例 / readyz 就绪探针 |
| 14 | 密钥/配置管理 | `.env.example` + `config.py` validator + 生产环境硬约束 | SECRET_KEY 在生产环境强制校验，禁止默认值；密钥 rotation 流程 |

## 面试讲述模板

### Q: 你的 CI 怎么搭？
> 我用 GitHub Actions，4 个 job：lint（ruff）、test（pytest + 覆盖率阈值 35%）、build（Docker 镜像烟雾测试）、security（pip-audit）。同分支并发用 `cancel-in-progress` 取消旧任务，节省 CI 资源。覆盖率 XML 上传为 artifact 保留 14 天，便于回溯。CI 环境通过 `ENVIRONMENT=ci` + 空 API Key 触发 MockLLM/SQLite 回退路径，不依赖真实密钥。

### Q: 线上出问题怎么排查？
> 我用 OpenTelemetry 做全链路追踪。FastAPI 自动埋点每个 HTTP 请求生成 span，httpx 自动埋点 LLM/外部 HTTP 调用，SQLAlchemy 自动埋点 DB 查询，Redis 自动埋点缓存/Pub/Sub 操作。LangGraph 节点通过 `_with_node_metrics` 包装手动开 span，与 HTTP span 串联。trace_id 通过 OTel SpanProcessor 同步到 structlog contextvar，让所有日志带同一 trace_id。排查时拿到前端报的 X-Request-ID（响应头回传），在日志系统按 trace_id 过滤即可看到完整链路。

### Q: 数据库 schema 怎么演进？
> 我用 Alembic 做版本化迁移。`init_db()` 已标记 deprecated，生产环境必须 `alembic upgrade head`。存量环境用 `alembic stamp 0001_init` 标记基线，跳过 DDL 直接进入版本治理。生产迁移流程：`alembic upgrade head --sql > preview.sql` 预演 → DB 快照 → 执行 → 验证 `alembic_version` 表 → 准备 downgrade 回滚预案。autogenerate 必须人工审阅，可能漏判 server_default。

### Q: 配置与密钥怎么管？
> 我用 pydantic-settings + `.env` 文件。`.env.example` 是模板，`.env` 在 `.gitignore` 里不会提交。生产环境通过 `SECRET_KEY` 字段 validator 强制校验：不能是默认值且长度 >=32，否则启动失败。CI 环境 `ENVIRONMENT=ci` 跳过校验便于无密钥跑通。密钥 rotation 流程：生成新 SECRET_KEY → 灰度发布新实例 → 旧 token 自然过期 → 旧实例下线。

## 部署清单

### 新环境部署
```bash
# 1. 复制环境变量模板并填入实际值
cp .env.example .env
vim .env

# 2. 安装依赖
cd backend && pip install -r requirements.txt

# 3. 应用数据库迁移
alembic upgrade head

# 4. 启动应用
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. 验证健康
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```

### 存量环境接入 Alembic
```bash
# 1. 备份现有 DB
pg_dump edu_agent_db > backup_$(date +%Y%m%d).sql

# 2. 标记基线（不执行 DDL，仅写入 alembic_version 表）
alembic stamp 0001_init

# 3. 验证
alembic current
# 输出应为: 0001_init (head)

# 4. 后续所有 schema 变更走 alembic revision + upgrade
```

### 启用 OTel 收集
```bash
# 1. 启动 Jaeger collector（Docker）
docker run -d -p 4317:4317 -p 16686:16686 jaegertracing/all-in-one:1.57

# 2. 在 .env 中配置
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_TRACES_EXPORTER=otlp

# 3. 重启应用，访问 http://localhost:16686 查看 trace
```
