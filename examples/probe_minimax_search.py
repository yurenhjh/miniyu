"""
probe_minimax_search.py — MiniMax-M2.1 × enable_search 行为探针（真实 API，不计入 pytest）

现象（Web UI 真机）：qwen3.6-flash + 联网开正常返回带搜索数据的回答；
MiniMax-M2.1 + 联网开 → Agent 15 步全空响应（无 reasoning / 无正文）→ max_steps 终止。
本脚本隔离变量：同一 prompt 下对比 MiniMax 三种请求形态，定位空响应的触发条件。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.agent import SYSTEM_PROMPT
from core.agent_config import load_config
from core.os_service_api import OSServiceAPI

MODEL = "MiniMax-M2.1"
QUERY = "今天北京天气怎么样？"


def _agent_tools():
    api = OSServiceAPI()
    tools = api.list_tools_openai()
    return tools


def stream_call(cfg, label, enable_search, tools=None, system_prompt=None):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or "你是一个助手。"},
            {"role": "user", "content": QUERY},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": True,
    }
    if enable_search:
        body["enable_search"] = True
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    print(f"\n{'=' * 22} {label} {'=' * 22}")
    try:
        r = requests.post(
            f"{cfg['llm']['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['llm']['api_key']}"},
            json=body, timeout=120, stream=True,
        )
    except Exception as e:
        print(f"[网络异常] {e}")
        return

    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"错误响应: {r.text[:500]}")
        return

    n_reason, n_text, n_tools, finish, extra_keys = 0, 0, 0, None, set()
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        for ch in obj.get("choices", []):
            delta = ch.get("delta", {}) or {}
            extra_keys.update(k for k in delta.keys() if k not in
                              ("role", "content", "reasoning_content", "tool_calls"))
            n_reason += len(delta.get("reasoning_content") or "")
            n_text += len(delta.get("content") or "")
            if delta.get("tool_calls"):
                n_tools += len(delta["tool_calls"])
            if ch.get("finish_reason"):
                finish = ch.get("finish_reason")
    print(f"reasoning 字数: {n_reason} | content 字数: {n_text} | tool_calls: {n_tools} | finish_reason: {finish}")
    if extra_keys:
        print(f"delta 额外字段: {sorted(extra_keys)}")
    if n_text == 0 and n_reason == 0 and n_tools == 0:
        print(">>> 空响应（正文/思考/工具调用均为空）")


def main():
    cfg = load_config()
    print(f"端点: {cfg['llm']['base_url']} | 探测模型: {MODEL}")
    stream_call(cfg, "① 流式 + enable_search + 全部真实工具 + 真实 SYSTEM_PROMPT（Agent 联网开同款）",
                True, tools=_agent_tools(), system_prompt=SYSTEM_PROMPT)
    stream_call(cfg, "② 流式 + 无 enable_search + 全部真实工具 + 真实 SYSTEM_PROMPT（Agent 联网关同款）",
                False, tools=_agent_tools(), system_prompt=SYSTEM_PROMPT)


if __name__ == "__main__":
    main()
