@echo off
chcp 65001 > nul
echo ====================================================================
echo  [EduAgent-Platform] 正在启动 FastAPI 后端服务...
echo  接口文档访问: http://localhost:8001/docs
echo ====================================================================
cd /d "%~dp0backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

pause
