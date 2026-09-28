# 认证

## 三种认证方式

| 方式 | 适用场景 | Header |
|---|---|---|
| JWT | 用户登录 | `Authorization: Bearer <token>` |
| API Key | 服务间调用 | `X-API-Key: <key>` |
| 匿名访问 | dev/ci 环境 | `X-User-Id: <user_id>` |

## JWT

- 算法：HS256
- 过期：24 小时
- Claim：`sub` (user_id), `tenant_id`, `role`, `jti` (唯一 ID)
- 失效：登出时 jti 加入 Redis 黑名单

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "teacher", "password": "xxx"}'
# {"access_token": "eyJ...", "token_type": "bearer"}

curl -H "Authorization: Bearer eyJ..." http://localhost:8000/api/v1/agents/sync-run
```

## API Key

服务间调用（如前端 BFF 调后端）：

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/v1/agents/sync-run
```

## 匿名访问

仅 `ENVIRONMENT` 为 `development`/`ci`/`staging` 时允许：

```bash
curl -H "X-User-Id: u-001" http://localhost:8000/api/v1/agents/sync-run
```

## 权限矩阵

| 角色 | 可访问端点 |
|---|---|
| teacher | agents/run, agents/sync-run, chat/* |
| admin | 全部 + users/*, audit-logs/* |
| service | agents/run (API Key), rag/kb/* |
