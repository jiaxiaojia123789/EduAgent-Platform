@echo off
chcp 65001 > nul
echo ====================================================================
echo  [EduAgent-Platform] 正在启动 豆包风格 Next.js 前端服务...
echo  浏览器访问地址: http://localhost:3000
echo ====================================================================
cd /d "%~dp0frontend"
if not exist node_modules (
    echo [提示] 正在首次安装前端依赖，请稍候...
    call npm install
)
npm run dev
pause
