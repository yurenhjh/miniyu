#!/usr/bin/env bash
# miniyu - Desktop AI Assistant (Terminal UI, 类 cosh 交互)
# Linux/macOS 启动脚本。Windows 用 run_miniyu_cli.bat。
set -euo pipefail
cd "$(dirname "$0")"

echo "================================================"
echo "  miniyu - Desktop AI Assistant (CLI)"
echo "================================================"
echo "  First run? Setup:"
echo "    bash setup_linux.sh"
echo "  No config = deterministic offline mode"
echo

PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" examples/agent_cli.py