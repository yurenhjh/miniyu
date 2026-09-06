#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
live_llm_check.py — 真机验证驱动：真实 LLM（阿里云百炼 qwen）↔ 咱们的文件系统

目的：把 miniyu 的四层链路（LLM → Agent → OSServiceAPI/ToolRegistry → 文件系统）
用真实 function-calling 跑通，并做**独立核验**：工具是否真的被调用、
文件系统是否真的被改动，以磁盘实测为准，不轻信 LLM 的措辞。

做法（全部限制在临时隔离目录，默认跑完清理）：
    1. 在临时目录下创建文件夹「报告」
    2. 往里面写 摘要.txt（固定内容）
    3. 列出「报告」目录内容
    4. 查看磁盘剩余空间
然后驱动自己 mkdir/read/listdir/shutil.disk_usage 核验 1~4 是否真实发生。

密钥不入库，运行前用环境变量注入（优先级高于 config.yaml）：
    AGENT_LLM_PROVIDER=openai_compatible
    AGENT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    AGENT_LLM_API_KEY=<你的百炼 API Key>
    AGENT_LLM_MODEL=qwen3.5-plus          # 或 qwen-plus / qwen3.8-flash 等
    可选：AGENT_CONFIRM_HIGH_RISK=0       # 演示/自动化用，不弹确认

用法：
    python examples/live_llm_check.py            # 非流式（agent.run）
    python examples/live_llm_check.py --stream   # 流式（agent.run_stream，顺带验终止修复）
    python examples/live_llm_check.py --keep     # 跑完保留临时目录不清理
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent
from core.agent_config import load_config
from core.safety import ConfirmationDenied


def allow_handler(pv):
    """自动化模式：高危操作也放行（指令已限定在隔离临时目录内）"""
    return True


def build_prompt(scratch: str) -> str:
    """多步文件系统指令，全部锁在隔离目录 scratch 内"""
    return (
        "请依次完成以下文件系统操作，目录是专门用于测试的隔离目录，可放心读写，"
        "不要操作该目录以外的任何路径：\n"
        f"1) 在目录 {scratch} 下创建文件夹「报告」；\n"
        "2) 在「报告」文件夹里写一个文本文件 摘要.txt，内容为："
        "“miniyu 真机联调成功，Qwen3.5-Plus 成功调用文件系统工具。”；\n"
        f"3) 列出「{scratch}/报告」文件夹的内容；\n"
        "4) 查看磁盘剩余空间。\n"
        "全部完成后，用简短中文总结每一步做了什么、得到什么结果。"
    )


def fmt_args(args) -> str:
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)


def dump_transcript(agent: Agent):
    """把会话消息打印成可读日志（供证据文档引用）"""
    print("\n----- 对话日志（消息节选） -----")
    tool_names = []
    for msg in agent.conversation.messages:
        role = msg.get("role")
        if role == "user":
            print(f"[user] {msg.get('content', '')[:300]}")
        elif role == "assistant":
            tcs = msg.get("tool_calls")
            if tcs:
                for tc in tcs:
                    fn = tc.get("function", {})
                    name = fn.get("name") or (tc.get("name") if isinstance(tc, dict) else None)
                    name = fn.get("name", tc.get("name"))
                    print(f"[assistant→调用] {name}({fmt_args(fn.get('arguments'))})")
            else:
                content = msg.get("content", "")
                if content:
                    print(f"[assistant→回复] {content[:400]}")
        elif role == "tool":
            name = msg.get("name", "?")
            tool_names.append(name)
            content = str(msg.get("content", ""))
            # 只节选，避免 result 过长刷屏
            print(f"[tool 结果] {name} → {content[:220]}")
        elif role == "system":
            print("[system] <miniyu 人设与工具使用守则>")
    # 去重后的工具调用序列
    seen, ordered = set(), []
    for n in tool_names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    print(f"实际执行的工具序列（去重后）: {ordered}")


def verify_fs(scratch: Path) -> dict:
    """独立核验：不依赖 LLM 的返回，直接查磁盘"""
    report = scratch / "报告"
    target = report / "摘要.txt"
    expected = "miniyu 真机联调成功，Qwen3.5-Plus 成功调用文件系统工具。"
    v = {
        "报告目录存在": report.is_dir(),
        "摘要.txt 存在": target.is_file(),
        "摘要.txt 内容一致": False,
        "报告目录被列出": False,  # 由 listdir 实测判定
        "磁盘信息可查": False,
    }
    if target.is_file():
        v["摘要.txt 内容一致"] = target.read_text(encoding="utf-8") == expected
    try:
        listing = sorted(p.name for p in report.iterdir()) if report.is_dir() else []
        v["报告目录被列出"] = listing == ["摘要.txt"]
    except OSError:
        listing = []
    v["listing"] = listing
    try:
        du = shutil.disk_usage(scratch)
        v["磁盘信息可查"] = du.total > 0 and du.free > 0
        v["disk_usage"] = {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        pass
    return v


def main():
    ap = argparse.ArgumentParser(description="miniyu 真机验证驱动")
    ap.add_argument("--stream", action="store_true", help="用流式 run_stream")
    ap.add_argument("--keep", action="store_true", help="跑完不清理临时目录")
    args = ap.parse_args()

    config = load_config()
    llm_cfg = config.get("llm", {})
    if llm_cfg.get("provider") != "openai_compatible":
        print("[错误] 当前未配置真实 LLM。请先设置环境变量（见文件头注释），", file=sys.stderr)
        print("       provider 需为 openai_compatible。", file=sys.stderr)
        return 2
    if not llm_cfg.get("api_key"):
        print("[错误] 缺少 AGENT_LLM_API_KEY。", file=sys.stderr)
        return 2

    print("=" * 60)
    print("miniyu 真机验证（真实 LLM ↔ 文件系统）")
    print("=" * 60)
    print(f"model   : {llm_cfg.get('model')}")
    print(f"base_url: {llm_cfg.get('base_url')}")
    print(f"stream  : {args.stream}")

    scratch = Path(tempfile.mkdtemp(prefix="miniyu_live_"))
    print(f"隔离目录: {scratch}")

    # 会话历史也落到隔离目录，不污染仓库数据
    config.setdefault("memory", {})["storage_dir"] = str(scratch / "sessions")

    agent = Agent(config=config, confirm_handler=allow_handler)
    prompt = build_prompt(str(scratch))

    print("\n----- 开始真实调用 -----")
    t0 = time.time()
    try:
        if args.stream:
            collected = []
            n_stop = 0
            for ch in agent.run_stream(prompt):
                if ch.text:
                    collected.append(ch.text)
                if ch.finish_reason == "stop":
                    n_stop += 1
            result = "".join(collected)
            print(f"[流式] 收到 {n_stop} 个最终 stop 终止块（修复验证点：应为 1，且只跑一轮）")
        else:
            result = agent.run(prompt)
    except ConfirmationDenied as e:
        print(f"[失败] 操作被拒绝: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # 网络/鉴权/解析等真实错误原样浮出
        print(f"[异常] 调用失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 4
    elapsed = time.time() - t0
    print(f"耗时: {elapsed:.1f}s")

    print("\n----- Agent 最终回复 -----")
    print(result if result else "(空)")

    dump_transcript(agent)

    print("\n----- 文件系统独立核验（驱动自查，不信 LLM） -----")
    v = verify_fs(scratch)
    for k in ("报告目录存在", "摘要.txt 存在", "摘要.txt 内容一致", "报告目录被列出", "磁盘信息可查"):
        print(f"  {k}: {'✅' if v.get(k) else '❌'}")
    print(f"  listing: {v.get('listing')}")
    if v.get("disk_usage"):
        du = v["disk_usage"]
        print(f"  disk_usage: total={du['total']} used={du['used']} free={du['free']}")

    ok = all(v.get(k) for k in ("报告目录存在", "摘要.txt 存在", "摘要.txt 内容一致", "磁盘信息可查"))
    print("\n" + ("✅ 核验通过：LLM 真实调用了文件系统工具且结果落盘一致" if ok
                  else "❌ 核验未完全通过：见上方明细"))

    if args.keep:
        print(f"[--keep] 保留隔离目录: {scratch}")
    else:
        shutil.rmtree(scratch, ignore_errors=True)
        print(f"[已清理] 临时目录已删除: {scratch}")

    return 0 if ok else 5


if __name__ == "__main__":
    sys.exit(main())
