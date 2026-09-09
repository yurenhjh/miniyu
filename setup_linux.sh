#!/usr/bin/env bash
# ============================================================
# miniyu - Linux 一键配置脚本（Ubuntu 22.04+ / Debian 系）
# 作用：① 装系统级依赖（桌面自动化 X11 工具，可选、失败不阻塞）
#       ② 建 .venv 虚拟环境并装 Python 依赖（绕开 Ubuntu 24.04+ 的
#          PEP 668 externally-managed-environment，不污染系统 Python）
#       ③ 从模板复制 config.yaml（若还没有）
#       ④ 检测 Wayland/Xorg，给出可移植性指引
# 用法：bash setup_linux.sh [--no-apt]     # --no-apt=跳过系统工具安装
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; NC=$'\e[0m'

say(){ printf "%b\n" "$*"; }
ok(){  say "${GRN}[OK]${NC} $*"; }
warn(){ say "${YEL}[!]${NC} $*"; }
err(){ say "${RED}[×]${NC} $*"; }

# 可选参数：--no-apt 跳过系统工具安装
SKIP_APT=0
for arg in "$@"; do
  case "$arg" in
    --no-apt) SKIP_APT=1 ;;
    *) warn "未知参数：$arg（忽略）" ;;
  esac
done

say "=================================================="
say "  miniyu - Linux environment setup"
say "=================================================="

# ---------- 0. Python 检查 ----------
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    err "找不到 python3，请先安装：sudo apt-get install -y python3 python3-pip python3-venv git"
    exit 1
fi

# ---------- 1. 系统级依赖（尽力而为，失败不阻塞后续）----------
# xdotool/wmctrl/xclip 是 X11 工具（窗口/点击/文本输入/剪贴板）；
# gnome-screenshot / scrot 是截图（运行时按 gnome-screenshot→scrot→import 顺序自动探测）。
# 仅用"文件管理 / Web 对话"可整步跳过（bash setup_linux.sh --no-apt）。
say ""
say "${YEL}步骤 1/4${NC} 安装系统级依赖（可能需要 sudo 密码；--no-apt 可跳过）..."
if [ "$SKIP_APT" -eq 1 ]; then
    warn "已跳过系统工具安装（--no-apt）。桌面自动化（找窗口/点击/截图/剪贴板）将不可用。"
elif command -v apt-get >/dev/null 2>&1; then
    # a) 必备：窗口/输入/剪贴板工具
    if ! sudo apt-get update >/dev/null 2>&1; then
        warn "apt-get update 失败（无 sudo 权限/离线？）。系统工具安装中止，其余继续。"
    else
        if ! sudo apt-get install -y xdotool wmctrl xclip >/dev/null 2>&1; then
            warn "xdotool/wmctrl/xclip 安装失败，桌面自动化不可用（不影响 Web/CLI 对话）。"
        else
            ok "xdotool / wmctrl / xclip 已安装"
        fi
        # b) 截图：gnome-screenshot 在新版 Ubuntu 已移除，失败自动换 scrot
        if ! sudo apt-get install -y gnome-screenshot >/dev/null 2>&1; then
            if ! sudo apt-get install -y scrot >/dev/null 2>&1; then
                warn "截图工具都装失败，可稍后手动：sudo apt-get install -y scrot"
            else
                ok "scrot 已安装（gnome-screenshot 在仓库中不可用，自动改用 scrot）"
            fi
        else
            ok "gnome-screenshot 已安装"
        fi
    fi
else
    warn "未检测到 apt-get，请手动安装：xdotool wmctrl xclip + (scrot 或 gnome-screenshot)。"
fi

# ---------- 2. Python 依赖（建 .venv，绕开 PEP 668）----------
say ""
say "${YEL}步骤 2/4${NC} 建立 .venv 虚拟环境并安装 Python 依赖..."
if [ ! -x ".venv/bin/python" ]; then
    if ! "$PY" -m venv .venv; then
        err "创建虚拟环境失败。Debian/Ubuntu 需先装：sudo apt-get install -y python3-venv"
        exit 1
    fi
    ok ".venv 虚拟环境已创建"
else
    ok ".venv 已存在，复用"
fi
# 显式用 venv 里的 python/pip，绝不碰系统 Python（Ubuntu 24.04+ 会因 PEP 668 拒绝）
".venv/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
if ! ./.venv/bin/pip install -r requirements.txt; then
    err "Python 依赖安装失败，请检查网络后重试。"
    exit 1
fi
ok "Python 依赖已安装（含 Web 界面所需 flask）"

# ---------- 3. 复制配置文件 ----------
say ""
say "${YEL}步骤 3/4${NC} 准备 config.yaml ..."
if [ -f config.yaml ]; then
    warn "config.yaml 已存在，跳过复制（避免覆盖你已填的 key）。"
    warn "  若想拿含全部段（email/web_search/fallback 等）的最新模板，可自行："
    warn "    cp config.yaml.example config.yaml   # 再从模板填你的 key"
else
    cp config.yaml.example config.yaml
    ok "已从模板生成 config.yaml"
fi
say "  请编辑 config.yaml，填入你自己的："
say "    - llm.api_key / llm.base_url / llm.model（主对话 API，必填才有在线对话）"
say "    - llm.supports_vision（主对话模型有视觉就留 true，无视觉见文件内注释）"
say "    - email.* / fallback.*（可选，见文件内注释）"

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
say "  完成！接下来二选一启动（启动脚本会自动用 .venv 里的 Python）："
say "    bash run_miniyu_web.sh   # Web 界面（推荐，浏览器用 Edge/Chrome）"
say "    bash run_miniyu_cli.sh   # 命令行交互（类 cosh）"
say "=================================================="
say "  💡 没填 api_key 也能启动，但只会进『离线模式』（只认预设指令）。"
