# 配置说明

## 配置加载优先级

```
.env > 环境变量 > settings.yaml > 默认值
```

## 关键环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ENVIRONMENT` | `development` | `ci`/`development`/`staging`/`production`/`loadtest` |
| `SECRET_KEY` | - | JWT 签名密钥，生产环境必须 ≥32 字符且非默认值 |
| `DASHSCOPE_API_KEY` | - | 阿里云百炼 API Key，留空触发 MockLLM |
| `LLM_PROVIDERS` | `dashscope` | 逗号分隔的供应商列表，按出现顺序作为优先级 |
| `OPENAI_FALLBACK_API_KEY` | - | OpenAI 兼容供应商兜底（如 vLLM/Together） |
| `OPENAI_FALLBACK_BASE_URL` | - | 兜底供应商的 base URL |
| `REDIS_HOST` | `localhost` | Redis 连接 host，留空触发 InProcEventBus |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `MILVUS_HOST` | `localhost` | Milvus host，留空触发 BM25 关键词兜底 |
| `DATABASE_URL` | `sqlite:///./data/edu.db` | SQLAlchemy 连接串 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | - | OpenTelemetry collector 端点，留空降级 stdout |
| `TENANT_DEFAULT_DAILY_CALLS` | `1000` | 默认租户日 LLM 调用上限 |
| `TENANT_DEFAULT_DAILY_TOKENS` | `2000000` | 默认租户日 token 总量上限 |
| `FEISHU_WEBHOOK_URL` | - | 飞书 Webhook 告警地址（成本告警） |

## 配置示例

### 开发环境（.env）

```env
ENVIRONMENT=development
SECRET_KEY=dev-secret-key-do-not-use-in-prod-32chars!
DASHSCOPE_API_KEY=sk-xxx
LLM_PROVIDERS=dashscope,openai_fallback
OPENAI_FALLBACK_API_KEY=sk-xxx
OPENAI_FALLBACK_BASE_URL=https://api.openai.com/v1
OPENAI_FALLBACK_DEFAULT_MODEL=gpt-4o-mini
REDIS_HOST=localhost
MILVUS_HOST=localhost
DATABASE_URL=sqlite+aiosqlite:///./data/edu.db
```

### 生产环境（.env）

```env
ENVIRONMENT=production
SECRET_KEY=<32+ 字符强随机字符串>
DASHSCOPE_API_KEY=sk-xxx
LLM_PROVIDERS=dashscope,openai_fallback
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<强密码>
MILVUS_HOST=milvus
DATABASE_URL=postgresql+asyncpg://eduagent:password@postgres:5432/eduagent
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
TENANT_DEFAULT_DAILY_CALLS=5000
TENANT_DEFAULT_DAILY_TOKENS=10000000
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

### CI 环境（GitHub Actions）

```env
ENVIRONMENT=ci
SECRET_KEY=ci-only-secret-key-32-chars-padding!!
DASHSCOPE_API_KEY=  # 留空触发 MockLLM
REDIS_HOST=localhost
MILVUS_HOST=  # 留空触发 BM25 兜底
DATABASE_URL=sqlite+aiosqlite:///./data/ci_test.db
OTEL_TRACES_EXPORTER=none
```

## 安全约束

- `SECRET_KEY` 在 `ENVIRONMENT=production` 时强制校验：非默认值 + ≥32 字符
- `.env` 文件已加入 `.gitignore`，不会提交
- `.env.example` 仅作模板，不含真实密钥
- 敏感字段（API Key/Password）在日志中自动脱敏（structlog `make_filtering_logger`）

## 多租户配额配置

通过 Redis hash 动态配置（运维可热更新）：

```bash
# 设置租户 tenant-a 配额
redis-cli HSET tenant:quota:tenant-a \
  tenant_id tenant-a \
  name "Tenant A" \
  daily_llm_calls_limit 5000 \
  daily_token_limit 10000000 \
  max_concurrent_tasks 10 \
  enabled 1

# TTL 5 分钟（自动从 DB 重新同步）
EXPIRE tenant:quota:tenant-a 300
```
