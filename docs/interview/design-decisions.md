# 技术选型理由

> 面试常见追问的标准答案，每项都基于项目实际落地。

## Q: 为什么选 LangGraph 不选 AutoGen/CrewAI？

| 维度 | LangGraph 1.x | AutoGen | CrewAI |
|---|---|---|---|
| 状态机 | 显式 StateGraph + 条件边 | 隐式对话流 | 角色 pipeline |
| HITL | 原生 `interrupt()` + `Command(resume=)` | 需自实现 | 不支持 |
| 持久化 | AsyncRedisSaver checkpointer | 无 | 无 |
| 并行 | Send fan-out + aggregate | 有限 | 顺序为主 |
| 流式 | config 注入 on_token 回调 | 需包装 | 不支持 |
| 调试 | LangGraph Studio 可视化 | 黑盒 | 黑盒 |

**回答模板**：

> 教学场景需要 4 个硬指标：① HITL 审批暂停/续跑（教师审批教案后才能定稿）② 多 agent 并行（一次提交"教案+试题+课件+量规"复合需求，9 agent 并行执行）③ 跨进程恢复（Celery worker 暂停后 FastAPI 进程续跑）④ 节点级可观测（每个 agent 的耗时/重试/质量分推到 trace）。LangGraph 是唯一同时满足的方案。AutoGen 的 HITL 要自己实现状态持久化，CrewAI 完全不支持 HITL，都会在生产场景下出问题。

## Q: 为什么用 AsyncRedisSaver 不用 SQL 持久化？

**回答模板**：

> 三个原因：① HITL 跨进程恢复要求共享存储，Celery worker 暂停后 FastAPI 进程要能续跑，SQL 虽能共享但 latency 高 ② 项目已有 Redis 连接（任务总线 + 响应缓存），复用零成本，AsyncRedisSaver 读写 < 5ms vs SQL 30ms+ ③ Redis 8+ 内置 RedisJSON/RediSearch，原生支持 JSON 文档存储和搜索索引，checkpointer 需要。降级链也设计了：Redis 不可用 → MemorySaver 进程内兜底，HITL 仍能工作（只是不能跨进程）。

## Q: 为什么从 LangGraph 0.2 升级到 1.x？

**回答模板**：

> 0.2 的 HITL 要用 `update_state + ainvoke(None)` 旧模式，三个痛点：① 状态更新和续跑分两步，中间崩溃会丢状态 ② 没有 `Command(resume=)` 语义，审批参数靠手动塞 state，类型不安全 ③ 没有 `interrupt()` 函数式暂停，必须配 `interrupt_before=["hitl_gate"]` 配置式声明。1.x 用 `interrupt({"type": "teaching_approval", ...})` 函数式暂停，`Command(resume={"approved": true})` 注入决策续跑，代码可读性提升 + 类型安全。版本矩阵也对齐了：langgraph 1.2.11 ↔ langgraph-checkpoint 4.2 ↔ langchain-core 1.6.3 ↔ langgraph-checkpoint-redis 0.5.2。

## Q: 为什么 tenant_id 用 contextvar 不用 ThreadLocal？

**回答模板**：

> FastAPI 全异步，ThreadLocal 在协程切换时会丢失上下文。ContextVar 是 Python 3.7+ 标准库，asyncio task 友好。中间件入口 set 一次，业务代码任意位置 get，零侵入。配合 TenantContextMiddleware 做配额检查，超限直接 429 阻断，不消耗后端资源。

## Q: 断路器为什么不用 Redis 做共享状态？

**回答模板**：

> 故障隔离原则：断路器本身不能成为新的故障点。Redis 挂了断路器也得工作。进程内状态足够：每个 worker 独立观察上游健康，某个 worker 看到连续失败就自己熔断，不需要全局协调。简单可靠：threading.Lock + monotonic 时钟，无外部依赖。熔断状态导出到 /metrics 端点，Grafana 看板可视化，告警按 `edu_agent_circuit_breaker_state == 0` 触发。

## Q: LLM 响应缓存为什么用语义匹配？

**回答模板**：

> 精确匹配（SHA256 哈希）只命中完全相同的输入，但教师提问经常换说法：「设计导数几何意义教案」vs「请为导数几何意义写一份教案」，精确匹配命中率低。语义匹配用 embedding cosine 相似度 ≥ 0.92，能覆盖换说法场景。两层缓存：精确优先（最快），语义次之。黑名单拦截 UUID/时间戳/会话ID 等动态内容，避免缓存污染。temperature > 0.5 时不缓存（保证创造性输出）。命中率 35% 即可降 30% 成本。

## Q: 成本告警的多级响应怎么设计？

**回答模板**：

> 三级响应：80% warning（仅记录 structlog）→ 100% critical（应阻断，返回 429）→ 120% budget_exceeded（强制降级到便宜模型 qwen-turbo）。双层阈值：tenant 维度（日 200 万 token / 100 元）+ user 维度（日 10 万 token / 10 元）。Redis hash 累计，TTL 36h 跨天自动失效。告警钩子可注入飞书 Webhook / PagerDuty。同一阈值 5 分钟内不重复告警（去重窗口）。模型单价表覆盖 qwen 系列 + OpenAI，按 model 维度估算成本。

## Q: 多 agent 并行怎么聚合？

**回答模板**：

> LangGraph Send fan-out：intent_node 返回 `[Send("lesson_plan", state), Send("exam_quiz", state), ...]`，图自动并行调度。aggregate 节点接收所有 agent 的 sub_results，合并为 Markdown `\n\n---\n\n` 分隔。关键陷阱：单 agent 模式下 aggregate 仍要返回非空 dict，曾因返回 `{}` 触发 `InvalidUpdateError`（LangGraph 要求 reducer 输出非空）。修复方案：始终回写 `final_markdown_output` 和 `sub_results` 两个无副作用 state key。

## Q: A/B 实验怎么稳定分桶？

**回答模板**：

> `SHA256(experiment_name + conversation_id) % 100` 按权重累积分布选变体。同一 conversation_id 始终命中同一变体（稳定分桶），避免结果跳变。变体可指定不同 prompt_id / agent / model / temperature。结果按 (experiment, variant) 聚合：成功率/平均延迟/质量分/token 总量，通过 `/api/v1/agents/experiments` 端点暴露。内置 2 个实验（教案 prompt 精简 vs 详细、单 agent vs 双 agent 协作），默认关闭，运维动态开启。

## Q: 沙箱怎么防逃逸？

**回答模板**：

> 三层防护：① AST 静态分析拦截 forbidden imports（os/sys/subprocess/socket 等）+ 高危函数调用（eval/exec/__import__）② Linux setrlimit 限制 CPU 5s / 内存 256MB / 文件 1MB / 进程数 1 ③ unshare --net 隔离网络命名空间，子进程没有网络接口（完全离线）。Windows 降级为软限制（仅 subprocess.timeout 兜底），生产环境必须在 Linux 上跑。教学代码批改场景默认完全离线，白名单为空。

## Q: CI 怎么无密钥跑通？

**回答模板**：

> MockLLM 模式：DASHSCOPE_API_KEY 留空 → bailian_client 自动切 mock 返回固定文本 + 固定 token usage。Milvus 不可用 → BM25 关键词检索兜底。Redis 不可用 → MemorySaver 进程内 checkpointer + InProcEventBus 进程内事件总线。pytest fixture 自动 mock 全部 agent + grounding，无 GPU/API Key 依赖。32 个测试在 CI 上全绿，仅 test_code_grader_agent_workflow 因 DashScope 免费额度耗尽持续失败（与代码无关）。
