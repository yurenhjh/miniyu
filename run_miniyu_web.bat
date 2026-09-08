@echo off
rem miniyu - Desktop AI Assistant (Web UI)
rem Note: keep this file pure ASCII. cmd.exe parses .bat as the legacy
rem codepage (GBK on Chinese Windows) BEFORE chcp takes effect, so any
rem UTF-8 Chinese in this file would garble. English only avoids encoding
rem issues entirely.
title miniyu - Desktop AI Assistant (Web UI)

cd /d "%~dp0"

echo =============================================
echo   miniyu - Desktop AI Assistant (Web UI)
echo =============================================
echo.
echo   First run? Install dependencies:
echo     pip install -r requirements.txt
echo.
echo   To use a real LLM, edit config.yaml first:
echo     llm.provider: openai_compatible
echo     llm.base_url / llm.api_key / llm.model
echo   (or set AGENT_LLM_* env vars, see config.yaml comments)
echo.
echo   No config = deterministic offline mode (works on any machine)
echo.
python examples\miniyu_web.py
pause
