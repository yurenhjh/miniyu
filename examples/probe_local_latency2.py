"""
probe_local_latency2.py — 深挖两个疑点：
  1. qwen2.5-7b-abliterated 带 tools 的 400 具体报什么
  2. qwen3-4b 思考内容到底走什么字段（采样前 25 个原始 chunk 的 delta 键），
     以及含思考在内的真实 TTFB（采样窗口拉长到 90s）
  3. /no_think 软开关对 qwen3-4b 是否有效（禁思考后 TTFB/速度对比）
"""

import json
import time

import requests

BASE = "http://localhost:11434/v1"
STORY = "帮我写一段关于兔子和乌龟的故事"


def probe_raw(model, messages, tools=None, label="", window=90, nothink=False):
    if nothink and messages:
        messages = [dict(m) for m in messages]
        messages[-1] = dict(messages[-1])
        messages[-1]["content"] += " /no_think"
    body = {"model": model, "messages": messages, "stream": True, "max_tokens": 4096}
    if tools:
        body["tools"] = tools
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/chat/completions", json=body,
                          timeout=(5, 130), stream=True)
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        detail = ""
        if e.response is not None:
            detail = e.response.text[:300]
        print(f"  [{label}] ❌ HTTP {e.response.status_code}: {detail}")
        return
    ttfb = None
    n = 0
    chars = 0
    delta_keys_seen = {}
    first_content = ""
    t_start = None
    for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
        now = time.time()
        if t_start is None:
            t_start = now
        if now - t_start > window:
            break
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        for line in chunk.split("\n"):
            line = line.strip()
            if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                continue
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            delta = data.get("choices", [{}])[0].get("delta", {})
            for k, v in delta.items():
                if v:
                    delta_keys_seen[k] = delta_keys_seen.get(k, 0) + 1
            content = delta.get("content") or ""
            if content:
                if ttfb is None:
                    ttfb = now - t0
                n += 1
                chars += len(content)
                if len(first_content) < 200:
                    first_content += content
    r.close()
    dur = time.time() - t_start if t_start else 0
    tps = n / dur if dur > 0 else 0
    print(f"  [{label}] TTFB {ttfb if ttfb else -1:>6.1f}s | {n} tokens/{dur:.0f}s ≈ {tps:.1f} tok/s | "
          f"delta字段统计: {delta_keys_seen}")
    print(f"  [{label}] 开头: {first_content[:150]!r}")


# ---- 1. 7B + tools 的 400 详情 ----
print("=== qwen2.5-7b-abliterated + tools 的 400 详情 ===")
one_tool = [{"type": "function", "function": {
    "name": "get_disk_usage", "description": "查看磁盘使用情况", "parameters": {"type": "object", "properties": {}}}}]
try:
    r = requests.post(f"{BASE}/chat/completions",
                      json={"model": "richardyoung/qwen2.5-7b-instruct-abliterated:latest",
                            "messages": [{"role": "user", "content": "磁盘空间"}],
                            "tools": one_tool, "stream": False},
                      timeout=120)
    print(f"  状态: {r.status_code}")
    print(f"  响应: {r.text[:400]}")
except Exception as e:
    print(f"  ❌ {e}")

# ---- 2. qwen3-4b 思考字段 + 真实 TTFB ----
print("\n=== qwen3-4b 默认（思考开）裸负载，90s 窗口 ===")
probe_raw("huihui_ai/qwen3-abliterated:4b",
          [{"role": "user", "content": STORY}], label="qwen3默认", window=90)

# ---- 3. qwen3-4b /no_think ----
print("\n=== qwen3-4b + /no_think，90s 窗口 ===")
probe_raw("huihui_ai/qwen3-abliterated:4b",
          [{"role": "user", "content": STORY}], label="qwen3-no_think", window=90, nothink=True)
