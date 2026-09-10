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

# 优先用项目自带的虚拟环境（setup_linux.sh 创建）；没有才回退系统 python3/python。
# 不能只看 command -v：Windows 上 python3 常指向微软商店的占位程序
# （…\WindowsApps\python3.exe），文件存在、但一执行就静默退出（exit 49），
# 会让脚本毫无输出地失败。所以这里实际跑一次 -c 验证它真能用。
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then   # Windows 上的 venv 布局
    PY=".venv/Scripts/python.exe"
else
    PY=""
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "pass" >/dev/null 2>&1; then
            PY="$cand"
            break
        fi
    done
    if [ -z "$PY" ]; then
        echo "错误：找不到可用的 python3/python 解释器。" >&2
        echo "Windows 请改用 run_miniyu_cli.bat（.sh 是给 Linux/macOS 用的）。" >&2
        exit 1
    fi
fi

# 强制 UTF-8 输出：agent_cli.py 里有 emoji，中文 Windows 的 GBK 控制台
# 在输出被重定向/管道时会抛 UnicodeEncodeError（Linux 上无副作用）。
export PYTHONIOENCODING=utf-8
exec "$PY" examples/agent_cli.py