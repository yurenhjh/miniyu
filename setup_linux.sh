#!/usr/bin/env bash
# ============================================================
# miniyu - Linux 一键配置脚本（Ubuntu 22.04+ / Debian 系）
# 作用：① 装系统级依赖（桌面自动化 X11 工具 + 依赖）
#       ② 装 Python 依赖
#       ③ 从模板复制 config.yaml（若还没有）
#       ④ 检测 Wayland/Xorg，给出可移植性指引
# 用法：bash setup_linux.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; NC=$'\e[0m'

say(){ printf "%b\n" "$*"; }
ok(){  say "${GRN}[OK]${NC} $*"; }
warn(){ say "${YEL}[!]${NC} $*"; }
err(){ say "${RED}[×]${NC} $*"; }

say "=================================================="
say "  miniyu - Linux environment setup"
say "=================================================="

# ---------- 1. 系统级依赖 ----------
# xdotool/wmctrl/xclip 是 X11 工具（窗口/点击/文本输入/剪贴板）；
# gnome-screenshot 截图（无则回退 scrot / imagemagick）。
# 注意：仅需要"纯文件管理/Web LLM 对话"可跳过这些；要用桌面自动化再装。
say ""
say "${YEL}步骤 1/4${NC} 安装系统级依赖（可能需要 sudo 密码）..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y xdotool wmctrl xclip gnome-screenshot
    ok "系统依赖已安装"
else
    warn "未检测到 apt-get，请手动安装：xdotool wmctrl xclip gnome-screenshot（或 scrot/imagemagick）"
fi

# ---------- 2. Python 依赖 ----------
say ""
say "${YEL}步骤 2/4${NC} 安装 Python 依赖..."
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v pip3 >/dev/null 2>&1 && PIP=pip3 || PIP=pip
"$PIP" install -r requirements.txt
ok "Python 依赖已安装"

# ---------- 3. 复制配置文件 ----------
say ""
say "${YEL}步骤 3/4${NC} 生成 config.yaml ..."
if [ -f config.yaml ]; then
    warn "config.yaml 已存在，跳过复制（避免覆盖你已填的 key）"
else
    cp config.yaml.example config.yaml
    ok "已从模板生成 config.yaml"
    say "  请编辑 config.yaml，填入你自己的："
    say "    - llm.api_key / llm.model（主对话 API）"
    say "    - llm.supports_vision 与 vision_bridge（主模型无视觉时再填）"
    say "    - email.*（可选，发邮件才要）"
    say "    - fallback.*（可选，本地 Ollama 降级）"
fi

# ---------- 4. Wayland / Xorg 检测 ----------
say ""
say "${YEL}步骤 4/4${NC} 会话检测（桌面自动化前提）..."
SESS="${XDG_SESSION_TYPE:-unknown}"
case "$SESS" in
  wayland)
    warn "检测到 Wayland 会话：xdotool/wmctrl（X11 工具）在此会话下桌面自动化不可用。"
    say   "    如要用『桌面自动化』（找窗口/点击/截图），请在登录界面齿轮处选择『Ubuntu on Xorg』重新登录。"
    say   "    仅用『文件管理 + Web 聊天』则不受影响。"
    ;;
  x11|xorg) ok "Xorg 会话，桌面自动化可用。" ;;
  *) warn "无法确认会话类型（$SESS）。桌面自动化需 Xorg（X11）。" ;;
esac

# ---------- 完成 ----------
say ""
say "=================================================="
say "  完成！接下来二选一启动："
say "    bash run_miniyu_web.sh   # Web 界面（推荐，浏览器用 Edge/Chrome）"
say "    bash run_miniyu_cli.sh   # 命令行交互（类 cosh）"
say "=================================================="