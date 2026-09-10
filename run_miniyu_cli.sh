#!/usr/bin/env bash
# miniyu - Desktop AI Assistant (Terminal UI, 类 cosh 交互)
# Linux/macOS 启动脚本。Windows 用 run_miniyu_cli.bat。
set -euo pipefail
cd "$(dirname "$0")"

echo "================================================"
echo "  miniyu - Desktop AI Assistant (CLI)"
echo "================================================"
echo "  First run? Setup (auto-creates .venv, installs deps):"
echo "    bash setup_linux.sh"
echo "  No config = deterministic offline mode"
echo

# 优先用项目自带的虚拟环境（setup_linux.sh 创建）；没有才回退系统 python3
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY=python3
    command -v "$PY" >/dev/null 2>&1 || PY=python
fi

# 强制 UTF-8 输出：agent_cli.py 里有 emoji，中文 Windows 的 GBK 控制台
# 在输出被重定向/管道时会抛 UnicodeEncodeError（Linux 上无副作用）。
export PYTHONIOENCODING=utf-8
exec "$PY" examples/agent_cli.py