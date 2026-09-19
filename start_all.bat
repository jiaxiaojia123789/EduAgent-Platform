@echo off
chcp 65001 > nul
echo ====================================================================
echo  [EduAgent-Platform] 一键启动教育中台 (后端 + 前端)
echo ====================================================================
echo 1. 正在启动 FastAPI 后端服务 (端口 8001)...
start "EduAgent Backend (FastAPI)" cmd /c "%~dp0start_backend.bat"

echo 2. 正在启动 豆包风格 Next.js 前端服务 (端口 3000)...
start "EduAgent Frontend (Next.js)" cmd /c "%~dp0start_frontend.bat"

echo.
echo ====================================================================
echo  已在两个独立窗口中启动服务：
echo  - 前端 Web 访问地址: http://localhost:3000
echo  - 后端 API 文档地址: http://localhost:8001/docs

echo ====================================================================
