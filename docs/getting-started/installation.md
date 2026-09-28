# 安装与启动

## 环境要求

- Python 3.11+
- Node.js 18+ (前端构建)
- Docker 24+ + Docker Compose v2 (可选，用于完整依赖栈)
- Redis 8+ (生产) 或 Redis 7 (开发，会降级为 MemorySaver)
- PostgreSQL 16 (生产) / SQLite (开发，自动启用)

## 本地启动（最快路径）

### 1. 克隆仓库

```bash
git clone https://github.com/jiaxiaojia123789/EduAgent-Platform.git
cd EduAgent-Platform
```

### 2. 后端配置

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

pip install -r requirements.txt

# 配置环境变量
cp ../.env.example ../.env
# 编辑 .env 至少设置：
#   SECRET_KEY=<32+ 字符随机字符串>
#   DASHSCOPE_API_KEY=（留空则启用 MockLLM 模式，CI 无密钥可跑）
```

### 3. 启动依赖服务（Docker）

```bash
cd ../docker
docker-compose up -d redis postgres milvus
```

### 4. 启动后端

```bash
cd ../backend
# 初始化数据库（首次启动自动迁移）
python -m alembic upgrade head

# 启动 FastAPI
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000/docs 查看 OpenAPI 文档。

### 5. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 即可使用教学智能体。

## Docker Compose 一键启动

完整生产栈（含 Grafana + Prometheus + Celery worker）：

```bash
cd docker
docker-compose up -d
```

服务清单：

| 服务 | 端口 | 用途 |
|---|---|---|
| backend | 8000 | FastAPI 应用 |
| frontend | 5173 | Vue 前端 |
| redis | 6379 | checkpointer + 任务总线 + 响应缓存 |
| postgres | 5432 | 会话/审计日志 |
| milvus | 19530 | 向量库 |
| celery-worker | - | 异步任务执行 |
| prometheus | 9090 | 指标抓取 |
| grafana | 3000 | 可视化看板（admin/admin） |

## CI 环境（无密钥跑通）

```bash
# GitHub Actions 自动跑（推送到 main 即触发）
# 本地模拟 CI 环境：
$env:ENVIRONMENT="ci"
$env:DASHSCOPE_API_KEY=""  # 空 key 触发 MockLLM
$env:SECRET_KEY="ci-only-secret-key-32-chars-padding!!"
$env:DATABASE_URL="sqlite+aiosqlite:///./data/ci_test.db"
$env:REDIS_HOST="localhost"
$env:MILVUS_HOST=""  # 空 host 触发 BM25 关键词兜底
$env:OTEL_TRACES_EXPORTER="none"

python -m pytest tests/ -v
python -m ruff check app tests
```

## 故障排查

### `DashScope API 免费额度已耗尽`

- 现象：调用 `/api/v1/agents/sync-run` 返回 403 FreeTierOnly
- 原因：阿里云百炼免费额度用完
- 解决：充值百炼账户 / 关闭「仅使用免费层」/ 配置 OpenAI 兼容供应商兜底

### `Redis 缺少模块 {'ReJSON', 'search'}`

- 现象：日志告警，checkpointer 降级 MemorySaver
- 原因：Redis 7- 不含 RedisJSON/RediSearch
- 解决：升级到 Redis 8（`docker-compose.yml` 已配置 `redis:8-alpine`）

### `frontend/tsconfig.tsbuildinfo` untracked

- 这是 TypeScript 构建产物，已在 `.gitignore` 排除
- 如误提交：`git rm --cached frontend/tsconfig.tsbuildinfo`
