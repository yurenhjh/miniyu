#!/usr/bin/env bash
# miniyu - Desktop AI Assistant (Web UI)
# Linux/macOS 启动脚本。Windows 用 run_miniyu_web.bat。
set -euo pipefail
cd "$(dirname "$0")"

echo "================================================"
echo "  miniyu - Desktop AI Assistant (Web UI)"
echo "================================================"
echo "  First run? Setup (auto-creates .venv, installs deps):"
echo "    bash setup_linux.sh"
echo "    # 或手动（推荐 venv，Ubuntu 24.04+ 直接 pip 会被 PEP 668 拒绝）："
echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
echo "    cp config.yaml.example config.yaml   # 然后填你自己的 API key"
echo
echo "  No config = deterministic offline mode (works on any machine)"
echo

# 优先用项目自带的虚拟环境（setup_linux.sh 创建）；没有才回退系统 python3
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY=python3
    command -v "$PY" >/dev/null 2>&1 || PY=python
fi
exec "$PY" examples/miniyu_web.py