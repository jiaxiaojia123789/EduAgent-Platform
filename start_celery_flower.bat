@echo off
REM ============================================================
REM 启动 Flower 监控面板 - Celery 任务可视化
REM 默认 http://localhost:5555
REM ============================================================
cd /d e:\WorkBuddy\github_AI_education\backend

echo [INFO] Starting Flower monitoring dashboard...
echo [INFO] URL: http://localhost:5555
echo.

celery -A app.core.celery_app.celery_app flower --port=5555

pause
