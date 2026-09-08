"""
probe_bailian_search.py — 百炼服务端联网搜索探针（真实 API 实测，不计入 pytest）

背景：
  config.yaml 计划新增 bailian_tools.web_search 开关，启用后请求体加 enable_search=true，
  由阿里云服务端透明执行联网搜索。但官方文档未说明 enable_search 与自定义函数工具
  （tools / Function Calling）同请求混用时的行为——本脚本实测三类场景，其结果直接决定
  agent 层"是否可隐藏本地 browser_search 工具"的设计决策。

场景：
  A. enable_search=true，不带 tools —— 基线：服务端搜索是否生效
  B. enable_search=true + 完整真实工具列表（57 底层工具 + 组合技能）+ 真实 SYSTEM_PROMPT
     —— 混用是否报错？模型选服务端搜索还是调本地 browser_search？
  C. B 的基础上再加一轮 tool_calls/tool 历史（模拟 ReAct 循环中间态）
     —— 多轮工具历史 + 混用是否正常

判定依据（自动打印，人工综合判断）：
  - 响应体/message 中的额外字段（search_info、server_tool_use 等）
  - usage 里的搜索计数字段
  - 内容中的引用痕迹（[1] / 参考链接 / http）
  - 是否返回 tool_calls（模型选择了本地工具）
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.agent import SYSTEM_PROMPT
from core.agent_config import load_config
from core.os_service_api import OSServiceAPI

QUERY = "杭州今天的天气怎么样？"

# 本地 browser_search 工具的模拟返回（必应解析结果的样子，质量一般）
FAKE_LOCAL_SEARCH_RESULT = json.dumps({
    "query": "杭州今天天气",
    "engine": "bing",
    "results": [
        {"title": "杭州天气 - 天气网", "url": "http://www.weather.com.cn/weather/101210101.shtml",
         "snippet": "杭州今天多云，气温 25℃~33℃，东风 3 级，空气质量良。"},
    ],
    "text_snippet": "杭州今天多云，25℃~33℃，东风3级，空气质量良。",
}, ensure_ascii=False)


def call(cfg, label, extra_body=None, tools=None, messages=None):
    body = {
        "model": cfg["llm"]["model"],
        "messages": messages or [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": QUERY},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    if extra_body:
        body.update(extra_body)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    print(f"\n{'=' * 20} {label} {'=' * 20}")
    try:
        r = requests.post(
            f"{cfg['llm']['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['llm']['api_key']}"},
            json=body, timeout=90,
        )
    except Exception as e:
        print(f"[网络异常] {e}")
        return None

    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"错误响应: {r.text[:600]}")
        return None

    data = r.json()
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})

    # 1) 响应体额外字段（找 search_info / server_tool_use 一类）
    top_extra = {k: v for k, v in data.items()
                 if k not in ("choices", "usage", "model", "id", "created", "object")}
    print(f"顶层额外字段: {json.dumps(top_extra, ensure_ascii=False)[:300] if top_extra else '（无）'}")

    usage = data.get("usage", {})
    usage_extra = {k: v for k, v in usage.items()
                   if k not in ("prompt_tokens", "completion_tokens", "total_tokens")}
    print(f"usage 额外字段: {json.dumps(usage_extra, ensure_ascii=False)[:300] if usage_extra else '（无）'}")

    # 2) 模型是否选择本地工具
    tcs = msg.get("tool_calls") or []
    if tcs:
        for t in tcs:
            fn = t.get("function", {})
            print(f">>> 模型调用了本地工具: {fn.get('name')}({fn.get('arguments')})")
    else:
        print(">>> 模型未调用本地工具（直接回答）")

    # 3) 内容里的搜索痕迹
    content = msg.get("content") or ""
    markers = [m for m in ("search_info", "参考", "[1]", "http://", "https://") if m in content]
    print(f"内容搜索痕迹: {markers if markers else '（无）'}")
    print(f"内容预览: {content[:400]}")
    return data


def main():
    cfg = load_config()
    print(f"模型: {cfg['llm']['model']}  base_url: {cfg['llm']['base_url']}")

    # 可选：python probe_bailian_search.py A B C  —— 只跑指定场景
    only = set(sys.argv[1:]) or {"A", "A2", "B", "C", "D"}

    api = OSServiceAPI()
    full_tools = api.list_tools_openai() + api.list_skills_openai()
    tool_names = [t["function"]["name"] for t in full_tools]
    print(f"真实工具列表（{len(full_tools)} 个）: {', '.join(tool_names[:10])} ...")

    # ---- A2. 干净基线：极简 system prompt + enable_search，不带 tools ----
    # （A 用项目 SYSTEM_PROMPT 会被"工具守则"污染：模型无 tools 可调时仍自发输出
    #   文本协议的 browser_search 调用，无法判断搜索是否发生。A2 排除该干扰。）
    if "A2" in only:
        call(cfg, "A2. enable_search 干净基线（极简 prompt，无 tools）",
             extra_body={"enable_search": True},
             messages=[{"role": "system", "content": "你是一个助手。"},
                       {"role": "user", "content": QUERY}])

    # ---- D. 对照组：完整工具列表，但不开 enable_search ----
    # （预期模型调用本地 browser_search —— 用于证明 enable_search 开/关的差异）
    if "D" in only:
        call(cfg, "D. 对照：完整工具，不开 enable_search", tools=full_tools)

    # ---- A. 基线：enable_search，不带 tools（SYSTEM_PROMPT 会被工具守则污染）----
    if "A" in only:
        call(cfg, "A. enable_search 基线（无 tools）", extra_body={"enable_search": True})

    # ---- B. 混用：enable_search + 完整真实工具 ----
    call(cfg, "B. enable_search + 完整工具列表（混用）",
         extra_body={"enable_search": True}, tools=full_tools)

    # ---- C. 混用 + 一轮本地工具历史（ReAct 中间态）----
    # 模拟：用户问天气 → 模型调了 browser_search → 本地工具返回（质量一般的结果）→ 下一轮
    history = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": QUERY},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "browser_search",
                                      "arguments": "{\"query\": \"杭州今天天气\", \"top\": 6}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": FAKE_LOCAL_SEARCH_RESULT},
    ]
    call(cfg, "C. 混用 + 工具历史（ReAct 中间态）",
         extra_body={"enable_search": True}, tools=full_tools, messages=history)

    print("\n" + "=" * 60)
    print("实测结论请对照：")
    print("  1. A 无搜索痕迹 → 服务端搜索未生效（模型/参数问题）")
    print("  2. B 报 400     → 混用被拒 → agent 层需自适应降级")
    print("  3. B 模型直接答且有搜索痕迹 → 服务端搜索与工具混用生效，可放心隐藏本地 browser_search")
    print("  4. B 模型调用 browser_search → 混用不报错但模型偏好本地工具 → 更应隐藏本地工具")
    print("  5. C 报错 → 多轮工具历史 + 混用冲突 → ReAct 循环内需按轮次动态注入")


if __name__ == "__main__":
    main()
