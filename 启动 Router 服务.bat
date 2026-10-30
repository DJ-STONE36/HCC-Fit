@echo off
chcp 65001 >nul
rem 启动外部 Router 服务（HTTP，默认 http://127.0.0.1:8080）
rem 注意：桌面版已内置 Router，正常使用不需要本文件；只有“外部服务”模式或多端共用时才需要。
cd /d "%~dp0router"

python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo 缺少依赖：请先运行 python -m pip install -r requirements.txt
  pause
  exit /b 1
)

if "%SCIENCEPAL_API_KEY%"=="" echo [提示] 未设置 SCIENCEPAL_API_KEY：fast 可用，balanced 会降级为 fast。
if "%SCIENCEPAL_FILTER_AGENT_ID%"=="" echo [提示] 未设置 SCIENCEPAL_FILTER_AGENT_ID：balanced 需要它。
if "%HCCFIT_ROUTER_TOKEN%"=="" echo [提示] HCCFIT_ROUTER_TOKEN 为空：服务不鉴权（仅本机使用可以）。

echo 启动 Router：http://127.0.0.1:8080  （健康检查 /healthz，Ctrl+C 停止）
python -m uvicorn hccfit.app:app --host 127.0.0.1 --port 8080
pause
