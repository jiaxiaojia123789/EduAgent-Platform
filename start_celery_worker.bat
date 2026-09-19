@echo off
REM ============================================================
REM 启动 Celery Worker - EduAgent-Platform
REM 企业级分布式任务消费者进程
REM ============================================================
cd /d e:\WorkBuddy\github_AI_education\backend

echo [INFO] Starting Celery Worker for EduAgent-Platform...
echo [INFO] Queue: agent_heavy, default
echo [INFO] Concurrency: 4
echo [INFO] Broker: Redis at localhost:6379
echo.

REM Windows 下需要 eventlet / gevent 池，安装：pip install eventlet
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=4 -P eventlet -Q agent_heavy,default

pause
