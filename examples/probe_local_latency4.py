"""
probe_local_latency4.py — 用 agent 真实工具格式（parameters）补测：
  1. 7B 真实负载（system + 真实 57 工具）状态与 TTFB / 速度 / 是否调工具
  2. qwen3-4b 真实负载同上
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.agent import SYSTEM_PROMPT
from core.agent import Agent
from core.os_service_api import OSServiceAPI

BASE = "http://localhost:11434/v1"
STORY = "帮我写一段关于兔子和乌龟的故事"

agent = Agent(config={"llm": {"provider": "deterministic"},
                      "agent": {"max_steps": 3, "confirm_high_risk": False}})
TOOLS = agent._agent_openai_tools()
print(f"真实格式工具数: {len(TOOLS)}")
print(f"第一个工具的键: {list(TOOLS[0]['function'].keys())}")

for model in ["richardyoung/qwen2.5-7b-instruct-abliterated:latest",
              "huihui_ai/qwen3-abliterated:4b"]:
    print(f"\n=== {model}（真实负载，流式采样 45s） ===")
    body = {"model": model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": STORY}],
            "tools": TOOLS, "tool_choice": "auto",
            "stream": True, "max_tokens": 4096}
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/chat/completions", json=body,
                          timeout=(5, 150), stream=True)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"  ❌ HTTP {e.response.status_code}: {e.response.text[:300]}")
        continue
    ttfb = None
    fields = {}
    sample = ""
    tool_calls = {}
    t_start = None
    for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
        if t_start is None:
            t_start = time.time()
        if time.time() - t_start > 45:
            break
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        for line in chunk.split("\n"):
            line = line.strip()
            if not line.startswith("data: ") or "[DONE]" in line:
                continue
            try:
                d = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            delta = d.get("choices", [{}])[0].get("delta", {})
            for k in ("reasoning", "reasoning_content", "content"):
                if delta.get(k):
                    if ttfb is None:
                        ttfb = time.time() - t0
                    fields[k] = fields.get(k, 0) + 1
                    if k == "content" and len(sample) < 100:
                        sample += delta[k]
            for tc in delta.get("tool_calls") or []:
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_calls[fn["name"]] = fn.get("arguments", "")
    r.close()
    dur = time.time() - t_start if t_start else 0
    print(f"  TTFB(首个生成delta) {ttfb if ttfb else -1:>5.1f}s | {dur:.0f}s 内字段统计: {fields}")
    print(f"  工具调用: {list(tool_calls) or '无'}")
    print(f"  content 开头: {sample[:100]!r}")
    if fields.get("content"):
        cps = fields["content"] / dur
        print(f"  正文速度 ≈ {cps:.1f} tok/s")
