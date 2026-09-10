#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
miniyu — 终端对话界面

你的桌面 AI 助手。通过自然语言理解你的意图，
调用系统工具帮你完成文件管理、系统操作、应用控制等任务。

用法：
    python examples/agent_cli.py                        # 离线模式（默认）
    AGENT_LLM_PROVIDER=openai_compatible python examples/agent_cli.py   # 真实 LLM

内置命令：
    /reset      — 重置对话
    /model      — 查看当前使用的模型
    /tools      — 列出所有可用工具
    /config     — 查看当前配置
    /help       — 帮助
    /exit       — 退出
"""

import sys
import os
from pathlib import Path

# Windows 中文控制台默认 GBK，本文件里有 emoji，print 会抛 UnicodeEncodeError
# ——输出被重定向到文件/管道（IDE 运行窗口、python x.py > log.txt）时必现。
# 统一把 stdout/stderr 改成 UTF-8；Linux/macOS 本来就是 UTF-8，这里是空操作。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # 被替换成非 TextIOWrapper 时忽略
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent
from core.agent_config import load_config, set_config_authorization
from core.llm_client import DeterministicBrain, FailoverClient
from core.safety import ConfirmationDenied
from core.safety import AUTHZ_LEVELS, AUTHZ_LABELS, resolve_authz_level


# ============================================================
# miniyu 品牌标识
# ============================================================

BANNER = r"""
███    ███ ██ ███    ██ ██ ██    ██ ██    ██ 
████  ████ ██ ████   ██ ██  ██  ██  ██    ██ 
██ ████ ██ ██ ██ ██  ██ ██   ████   ██    ██ 
██  ██  ██ ██ ██  ██ ██ ██    ██    ██    ██ 
██      ██ ██ ██   ████ ██    ██     ██████

"""


def confirm_handler(pv):
    """高危操作确认：打印动作预告，等待用户输入"""
    print()
    print("=" * 54)
    print("  !  miniyu 需要你的确认")
    print("=" * 54)
    print(f"  工具: {pv['name']}")
    print(f"  风险: {pv['risk']}")
    print(f"  说明: {pv['description']}")
    if pv.get("params"):
        print(f"  参数: {pv['params']}")
    print("-" * 54)
    while True:
        answer = input("  miniyu > 是否执行？(y/n): ").strip().lower()
        if answer in ("y", "yes", "是", "允许"):
            print()
            return True
        if answer in ("n", "no", "不", "否", "拒绝"):
            raise ConfirmationDenied(pv.get("name", ""))
        print("  请输入 y 或 n。")


def print_banner():
    """打印启动横幅"""
    print(BANNER)
    print("=" * 54)
    print("  miniyu — 你的桌面 AI 助手")
    print("=" * 54)
    print("  输入指令开始操作，输入 /help 查看帮助")
    print()


def print_help(is_offline: bool):
    print()
    print("  miniyu 可用命令：")
    print("  " + "-" * 46)
    print("  /reset         重置当前对话")
    print("  /new           新建会话（对标 ChatGPT 的新对话）")
    print("  /sessions      列出所有会话")
    print("  /switch <id>   切换到指定会话")
    print("  /rename <id> <标题>  重命名会话")
    print("  /delete <id>   删除会话")
    print("  /model         查看当前使用的模型")
    print("  /authz         查看/切换授权档位（base|advanced|full）")
    print("  /tools         列出所有可用工具")
    print("  /config        查看当前配置")
    print("  /help          显示此帮助")
    print("  /exit          退出程序")

    if is_offline:
        print()
        print("  ⚠️  当前为离线模式，仅支持有限指令：")
        print("  " + "-" * 46)
        print("  '整理桌面'      — 按类型整理桌面文件")
        print("  '磁盘空间'      — 查看 C 盘使用情况")
        print("  '查看进程'      — 查看 CPU 占用最高进程")
        print("  '网络状态'      — 查看网络连接状态")
        print("  '查找大文件'    — 查找 C 盘大于 100MB 的文件")
        print("  '环境变量'      — 查看系统环境变量")
        print("  '帮助'          — 显示所有支持的功能")
        print()
        print("  💡 配置 API 后可体验完整智能服务：")
        print("     set AGENT_LLM_PROVIDER=openai_compatible")
        print("     set AGENT_LLM_BASE_URL=https://api.openai.com/v1")
        print("     set AGENT_LLM_API_KEY=sk-xxxx")
        print("     set AGENT_LLM_MODEL=gpt-4o-mini")
    else:
        print()
        print("  试试说：")
        print("  " + "-" * 46)
        print("  '整理桌面'      — 按类型整理桌面文件")
        print("  '磁盘空间'      — 查看 C 盘使用情况")
        print("  '查看进程'      — 查看 CPU 占用最高进程")
        print("  '帮我找一下名为报告的文件'  — 语义理解搜索")
    print()


def print_model_info(agent: Agent, config: dict):
    """打印模型信息"""
    is_offline = agent.provider == "deterministic"
    print(f"  模型: {agent.model_name}")

    if is_offline:
        print()
        print("  ⚠️  当前为离线模式，只能执行预设的简单指令。")
        print("  💡  配置 API 后可体验完整智能服务：")
        print("     编辑 config.yaml 或设置环境变量 AGENT_LLM_PROVIDER=openai_compatible")
        print()

    # 显示 FailoverClient 降级状态
    if isinstance(agent.llm, FailoverClient):
        status = agent.llm.status
        if status["degraded"]:
            print(f"  ⚠️  LLM 已降级（{status['last_error']}）")
            print(f"  🔄 将在 5 分钟后自动重试主 LLM")
            print()
        else:
            print(f"  ✅ 备用 LLM 已就绪（主 LLM 不可用时自动降级）")
            print()


def main():
    config = load_config()
    print_banner()

    # 创建 miniyu
    agent = Agent(
        config=config,
        confirm_handler=confirm_handler,
    )

    is_offline = agent.provider == "deterministic"

    # 显示当前模式
    print(f"  模式: {agent.model_name}")
    print(f"  视觉: {'支持' if agent.llm.supports_vision else '不支持'}")
    _authz_label = AUTHZ_LABELS[resolve_authz_level(config.get("agent", {}))]
    print(f"  授权档位: {_authz_label}（/authz 查看详情或切换）")

    # 显示 FailoverClient 状态
    if isinstance(agent.llm, FailoverClient):
        if agent.llm.status["degraded"]:
            print(f"  ⚠️  LLM 已降级: {agent.llm.status['last_error']}")
        else:
            print(f"  ✅ 自动降级已启用（主 LLM 不可用时切到本地 Ollama）")

    if is_offline:
        print()
        print("  ⚠️  离线模式仅支持有限指令，复杂需求请配置 API key。")
        print("     输入 /help 查看支持指令列表。")

    print()

    # 交互循环
    while True:
        try:
            user_input = input("miniyu > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("  再见！")
            break

        if not user_input:
            continue

        # 处理内置命令
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd == "/exit":
                print("  再见！")
                break
            elif cmd == "/reset":
                agent.reset()
                print("  miniyu 已重置当前对话。")
                continue
            elif cmd == "/new":
                sid = agent.create_session()
                print(f"  ✨ 已创建新会话: {sid}")
                continue
            elif cmd == "/sessions":
                sessions = agent.list_sessions()
                if not sessions:
                    print("  暂无会话。")
                    continue
                print(f"  miniyu 会话列表 ({len(sessions)} 个)：")
                print("  " + "-" * 46)
                for s in sessions:
                    marker = " ◀" if s.get("is_current") else ""
                    print(f"    [{s['id']}] {s['title']} ({s['message_count']} 条消息){marker}")
                print()
                continue
            elif cmd.startswith("/switch "):
                sid = user_input.split(" ", 1)[1].strip()
                if agent.switch_session(sid):
                    conv = agent.conversation
                    print(f"  ✅ 已切换到会话 [{sid}]: {conv.title} ({conv.total_messages} 条消息)")
                else:
                    print(f"  ❌ 会话 {sid} 不存在。输入 /sessions 查看所有会话。")
                continue
            elif cmd.startswith("/rename "):
                parts = user_input.split(" ", 2)
                if len(parts) < 3:
                    print("  用法: /rename <会话ID> <新标题>")
                    continue
                sid, title = parts[1], parts[2]
                if agent.rename_session(sid, title):
                    print(f"  ✅ 已重命名会话 [{sid}] 为: {title}")
                else:
                    print(f"  ❌ 会话 {sid} 不存在。")
                continue
            elif cmd.startswith("/delete "):
                sid = user_input.split(" ", 1)[1].strip()
                if sid == agent.current_session_id:
                    print("  ⚠️  不能删除当前会话，请先切换到其他会话。")
                    continue
                if agent.delete_session(sid):
                    print(f"  ✅ 已删除会话 [{sid}]")
                else:
                    print(f"  ❌ 会话 {sid} 不存在。")
                continue
            elif cmd == "/model":
                print_model_info(agent, config)
                # 如果当前是 FailoverClient，显示可用备用模型
                if isinstance(agent.llm, FailoverClient):
                    status = agent.llm.status
                    if status.get("fallback_model"):
                        print(f"  备用模型: {status['fallback_model']}")
                    print(f"  输入 /model switch <名称> 可切换备用模型")
                continue
            elif cmd.startswith("/model switch"):
                parts = user_input.split(None, 3)
                if len(parts) >= 3:
                    model_name = parts[2]
                    if agent.switch_fallback_model(model_name):
                        print(f"  ✅ 已切换备用模型为: {model_name}")
                    else:
                        print(f"  ❌ 切换失败，当前不是 FailoverClient 模式")
                else:
                    print("  用法: /model switch <模型名称>")
                    if isinstance(agent.llm, FailoverClient):
                        print(f"  当前备用模型: {agent.llm.status.get('fallback_model', '无')}")
                continue
            elif cmd.startswith("/authz"):
                # /authz 查看当前授权档位；/authz <base|advanced|full> 切换（持久化 + 热改运行中 agent）
                parts = user_input.split(None, 1)
                if len(parts) == 1:
                    lv = resolve_authz_level(config.get("agent", {}))
                    print(f"  当前授权档位: {AUTHZ_LABELS[lv]} ({lv})")
                    print("    base      = 基础授权：全部高危操作需确认（现状）")
                    print("    advanced  = 高级授权：仅永久删除文件需确认，其余高危（命令/杀进程/外发）自动放行")
                    print("    full      = 全自动：从不确认（含文件删除）")
                    print("  切换用法: /authz base | advanced | full")
                else:
                    level = parts[1].strip().lower()
                    if level not in AUTHZ_LEVELS:
                        print(f"  ❌ 未知档位: {level}（可选 base / advanced / full）")
                    else:
                        try:
                            set_config_authorization(level)
                        except Exception as e:
                            print(f"  ❌ 写回 config.yaml 失败: {e}（可手动编辑 agent.authorization 后重启）")
                            continue
                        agent.authz_level = level
                        config.setdefault("agent", {})["authorization"] = level
                        tip = {
                            "advanced": "，仅永久删除文件需确认",
                            "full": "，不再确认任何操作",
                            "base": "，全部高危操作需确认",
                        }[level]
                        print(f"  ✅ 已切换为「{AUTHZ_LABELS[level]}」授权{tip}（运行中生效，不丢会话）")
                continue
            elif cmd == "/tools":
                tools = agent.api.list_tools_mcp()
                print(f"  miniyu 可用工具 ({len(tools)} 个)：")
                for t in tools:
                    print(f"    - {t['name']}: {t.get('description', '')}")
                print()
                continue
            elif cmd == "/config":
                import json
                print(json.dumps(config, ensure_ascii=False, indent=2))
                continue
            elif cmd == "/help":
                print_help(is_offline)
                continue
            else:
                print(f"  未知命令: {cmd}，输入 /help 查看帮助。")
                continue

        # 执行 miniyu
        try:
            result = agent.run(user_input)
            print()
            print(f"  {result}")
            print()
        except ConfirmationDenied:
            print("  miniyu 操作已取消。")
        except Exception as e:
            print(f"  ! miniyu 出错了: {e}")


if __name__ == "__main__":
    main()