#!/usr/bin/env bash
# miniyu - Desktop AI Assistant (Web UI)
# Linux/macOS 启动脚本。Windows 用 run_miniyu_web.bat。
set -euo pipefail
cd "$(dirname "$0")"

echo "================================================"
echo "  miniyu - Desktop AI Assistant (Web UI)"
echo "================================================"
echo "  First run? Setup:"
echo "    bash setup_linux.sh        # 装依赖 / 复制配置"
echo "    # 或手动："
echo "    pip install -r requirements.txt"
echo "    cp config.yaml.example config.yaml   # 然后填你自己的 API key"
echo
echo "  No config = deterministic offline mode (works on any machine)"
echo

# 优先用 python3；没有则在 PATH 中找 python（Windows/WSL 混用场景）
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" examples/miniyu_web.py