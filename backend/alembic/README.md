# Alembic Migrations

本目录纳管所有数据库 schema 变更。**禁止手写 SQL 改表结构**。

## 常用命令

```bash
# 在 backend/ 目录下执行

# 1. 应用迁移到最新版本
alembic upgrade head

# 2. 生成新迁移脚本（基于模型 vs DB 差异）
alembic revision --autogenerate -m "add xxx column"

# 3. 查看当前版本
alembic current

# 4. 查看迁移历史
alembic history --verbose

# 5. 回退一版
alembic downgrade -1

# 6. 生成 SQL 预演脚本（不连 DB）
alembic upgrade head --sql > preview.sql
```

## 生产环境迁移流程

1. **预演**：在 staging 环境 `alembic upgrade head --sql > preview.sql`，人工审阅 SQL
2. **备份**：对生产 DB 做快照
3. **执行**：`alembic upgrade head`
4. **验证**：检查 `alembic_version` 表 + 关键表 schema
5. **回滚预案**：`alembic downgrade <target_rev>`，预先演练

## 约定

- **不自动生成 DDL 的场景**：数据迁移（DML）、复杂表结构变更、添加 NOT NULL 列需先加 NULL 列再回填再改 NOT NULL
- **命名**：脚本文件格式 `YYYY_MM_DD_HHMM_<shortrev>_<slug>.py`，slug 用英文
- **review**：所有 `revision --autogenerate` 必须人工审阅，可能漏判 server_default
- **删除列**：先标记 deprecated 一个版本，再删，避免回滚失败
