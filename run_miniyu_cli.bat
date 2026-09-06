@echo off
chcp 65001 >nul
echo.
echo  =============================================
echo   miniyu — 桌面 AI 助手（终端界面）
echo  =============================================
echo.
echo  首次使用请先安装依赖：pip install -r requirements.txt
echo.
echo  如需使用真实 LLM，设置环境变量后再运行：
echo    set AGENT_LLM_PROVIDER=openai_compatible
echo    set AGENT_LLM_BASE_URL=https://api.openai.com/v1
echo    set AGENT_LLM_API_KEY=sk-xxxx
echo    set AGENT_LLM_MODEL=gpt-4o-mini
echo.
echo  不配置即使用离线模式（零依赖，任何机器都能跑）
echo.
pause
python examples\agent_cli.py
pause