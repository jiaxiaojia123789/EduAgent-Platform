# EduAgent Grafana 监控说明

## 启动方式

```bash
# 在 docker/ 目录下执行
docker-compose up -d prometheus grafana

# 查看启动状态
docker-compose ps prometheus grafana
```

启动完成后，Grafana 会通过 provisioning 自动：
1. 注册 Prometheus 数据源（uid=prometheus，url=http://prometheus:9090）
2. 从 `/etc/grafana/dashboards` 加载 `eduagent-overview.json`
3. 将 `eduagent-overview` 设为默认首页 dashboard

## 访问

- Grafana UI：http://localhost:3000
  - 用户名 / 密码：`admin` / `admin`
  - 首页自动跳转到 `EduAgent Overview` dashboard
- Prometheus UI：http://localhost:9090
  - Status → Targets 可查看 `eduagent` job 的抓取状态
- 后端指标端点：http://localhost:8000/metrics

## Dashboard Panel 总览（2 列 × 4 行）

| 位置 | Panel | PromQL 核心 |
| --- | --- | --- |
| (0,0) | LLM 调用速率 (req/s) | `sum by (agent) (rate(edu_agent_llm_calls_total[5m]))` |
| (12,0) | P95 延迟 (ms) | `edu_agent_llm_latency_p95_ms` |
| (0,8) | LLM 缓存命中率 | `edu_agent_llm_cache_hit_rate` |
| (12,8) | 断路器状态 | `edu_agent_circuit_breaker_state` |
| (0,16) | LLM 供应商健康 | `edu_agent_llm_provider_state` + failures |
| (12,16) | Token 成本 (tokens/s) | `rate(edu_agent_llm_tokens_total[5m])` |
| (0,24) | Checkpointer 后端 | `edu_agent_checkpointer_backend` |
| (12,24) | 依赖连接状态 | `edu_agent_dependency_up` |

## 排查指南

### 1. Prometheus 抓取失败如何诊断

**现象**：Grafana panel 全部显示 "No data"，或所有 series 缺失。

**诊断步骤**：

1. 检查 Prometheus Targets 状态
   - 打开 http://localhost:9090/targets
   - 找到 `eduagent` job，确认 State = UP
   - 如果 State = DOWN：
     - Last Error 显示 "connection refused" → 后端容器未启动或未暴露 /metrics，检查 `docker logs edu-backend`
     - Last Error 显示 "i/o timeout" → 网络不通，检查 prometheus 与 backend 是否都在 `edu-agent-network`
     - Last Error 显示 "HTTP 404" → 后端路由未注册，检查 backend 是否挂载 `/metrics` 路由
2. 在 Prometheus UI 直接查询
   - 访问 http://localhost:9090/graph
   - 输入 `edu_agent_info`，应返回 1 条记录
   - 如果返回空 → 抓取间隔太短或后端没启动指标端点
3. 检查后端是否真的在输出指标
   ```bash
   curl http://localhost:8000/metrics
   ```
   - 应返回 `text/plain; version=0.0.4` 的 Prometheus exposition 格式
   - 包含 `# HELP edu_agent_info ...`、`# TYPE edu_agent_info gauge` 等行
4. 检查后端 SQL 聚合是否超时（见下条）

### 2. 指标聚合 SQL 慢时如何优化

**背景**：`/metrics` 端点每次抓取都会跑 `prompt_metrics.agent_stats(since_hours=24)`，对 `prompt_call_metrics` 表做聚合。当表数据量增长（百万级），抓取会变慢，导致 Prometheus 超时。

**诊断**：

```sql
-- 在 postgres 容器内执行
EXPLAIN ANALYZE
SELECT agent, COUNT(*) AS total_calls,
       AVG(latency_ms), PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)
FROM prompt_call_metrics
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY agent;
```

如果出现 `Seq Scan on prompt_call_metrics`（顺序扫描），说明缺索引。

**优化方案**（按收益排序）：

1. **添加复合索引**（首选）
   ```sql
   CREATE INDEX idx_prompt_metrics_agent_created
       ON prompt_call_metrics (agent, created_at DESC);
   CREATE INDEX idx_prompt_metrics_created
       ON prompt_call_metrics (created_at DESC);
   ```
2. **缩短聚合窗口**：将 `since_hours=24` 改为 `since_hours=1` 或 `6`，对应修改 `backend/app/core/metrics.py` 中 `prompt_metrics.agent_stats(since_hours=24)`。适合只需要观察近期趋势的场景。
3. **物化视图 / 汇总表**：对小时级聚合做预计算，写入 `prompt_metrics_hourly` 表，`/metrics` 只读汇总表。适合规模化生产环境。
4. **降低抓取频率**：在 `prometheus.yml` 把 `scrape_interval` 从 15s 调整到 30s/60s，减轻 SQL 压力（但会降低监控灵敏度）。
5. **缓存聚合结果**：在 Redis 中缓存 `agent_stats()` 结果 10-30s，`/metrics` 优先读缓存。

**验证**：执行 `curl http://localhost:8000/metrics -w "\n%{time_total}s\n"`，正常应 < 200ms；如果 > 2s 就需要优化。

### 3. 端口冲突

Grafana 默认 `3000:3000` 与 `frontend` 服务的主机端口冲突。两者不能同时启动。解决方式：

- 方案 A：仅启动监控栈，停掉 frontend：`docker-compose stop frontend && docker-compose up -d prometheus grafana`
- 方案 B：把 Grafana 改用其他主机端口，编辑 `docker/docker-compose.yml` 把 `"3000:3000"` 改为 `"3001:3000"`，然后访问 http://localhost:3001

### 4. 重置 Grafana 密码 / 数据

```bash
# 清空 grafana 数据卷，下次启动会重新加载 provisioning
docker-compose down -v grafana
docker volume rm docker_grafana_data
docker-compose up -d grafana
```
