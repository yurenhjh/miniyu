@echo off
rem miniyu - Desktop AI Assistant (Terminal UI)
rem Note: keep this file pure ASCII. cmd.exe parses .bat as the legacy
rem codepage (GBK on Chinese Windows) BEFORE chcp takes effect, so any
rem UTF-8 Chinese in this file would garble. English only avoids encoding
rem issues entirely.
title miniyu - Desktop AI Assistant (Terminal UI)

cd /d "%~dp0"

echo =============================================
echo   miniyu - Desktop AI Assistant (CLI)
echo =============================================
echo.
echo   First run? Install dependencies (in cmd from project root):
echo     py -m venv .venv
echo     .venv\Scripts\pip install -r requirements.txt
echo.
echo   To use a real LLM, edit config.yaml first:
echo     llm.provider: openai_compatible
echo     llm.base_url / llm.api_key / llm.model
echo   (or set AGENT_LLM_* env vars, see config.yaml comments)
echo.
echo   No config = deterministic offline mode (works on any machine)
echo.

rem Use UTF-8 for the console and for Python's stdout. Without this, Python
rem raises UnicodeEncodeError on a Chinese (GBK) Windows whenever stdout is a
rem pipe or a redirected file, because agent_cli.py prints emoji.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

rem Prefer the project venv created above; fall back to python on PATH
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
%PY% examples\agent_cli.py
pause
