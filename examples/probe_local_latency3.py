"""
probe_local_latency3.py — 定位三个遗留问题：
  1. 7B + 57 工具 400 的具体报错与元凶 schema
  2. qwen3-4b 全量工具负载下的 prompt eval 时间（首个 delta 的 TTFB，含思考）
  3. chat_template_kwargs.enable_thinking=false 能否关掉 qwen3 思考
  4. 7B 全量工具（若 400 修复）的 prompt eval + 生成速度
"""

import copy
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://localhost:11434/v1"
STORY = "帮我写一段关于兔子和乌龟的故事"

from core.os_service_api import OSServiceAPI

api = OSServiceAPI()
TOOLS = [{"type": "function", "function": t} for t in api.list_tools_mcp()]


def post(model, body, timeout=150):
    t0 = time.time()
    r = requests.post(f"{BASE}/chat/completions", json=body, timeout=(5, timeout))
    return r, time.time() - t0


# ---- 1. 400 报错详情 ----
print("=== 7B + 57 工具：400 详情 ===")
body = {"model": "richardyoung/qwen2.5-7b-instruct-abliterated:latest",
        "messages": [{"role": "user", "content": STORY}],
        "tools": TOOLS, "stream": False, "max_tokens": 100}
r, dt = post(body["model"], body)
print(f"  状态 {r.status_code} ({dt:.1f}s): {r.text[:500]}")

# ---- 2. 二分定位元凶工具 ----
if r.status_code == 400:
    print("\n=== 二分定位元凶工具 ===")
    lo, hi = 0, len(TOOLS)
    bad = []
    # 逐半测
    def test_tools(subset):
        b = {"model": "richardyoung/qwen2.5-7b-instruct-abliterated:latest",
             "messages": [{"role": "user", "content": "hi"}],
             "tools": subset, "stream": False, "max_tokens": 5}
        rr, _ = post(b["model"], b, timeout=60)
        return rr.status_code

    for i, t in enumerate(TOOLS):
        if test_tools([t]) != 200:
            bad.append((i, t["function"]["name"]))
    print(f"  元凶工具: {bad if bad else '单个都正常（组合问题？）'}")
    if bad:
        i = bad[0][0]
        print(f"  元凶 schema 示例:\n{json.dumps(TOOLS[i], ensure_ascii=False, indent=1)[:600]}")

# ---- 3. schema 清洗实验：去掉可疑字段后 7B 全量是否 200 ----
SUSPECT_KEYS = {"format", "default", "additionalProperties", "$schema", "examples",
                "exclusiveMinimum", "exclusiveMaximum", "pattern"}


def sanitize(schema):
    if isinstance(schema, dict):
        return {k: sanitize(v) for k, v in schema.items() if k not in SUSPECT_KEYS}
    if isinstance(schema, list):
        return [sanitize(x) for x in schema]
    return schema


clean = copy.deepcopy(TOOLS)
for t in clean:
    t["function"] = sanitize(t["function"])
print("\n=== 7B + 57 工具（清洗后） ===")
body = {"model": "richardyoung/qwen2.5-7b-instruct-abliterated:latest",
        "messages": [{"role": "user", "content": "磁盘空间"}],
        "tools": clean, "stream": False, "max_tokens": 200}
r, dt = post(body["model"], body)
print(f"  状态 {r.status_code} ({dt:.1f}s)")
if r.status_code == 200:
    msg = r.json()["choices"][0]["message"]
    print(f"  tool_calls: {[tc['function']['name'] for tc in msg.get('tool_calls', [])] or '无'}")
    print(f"  content: {(msg.get('content') or '')[:100]!r}")

# ---- 4. qwen3 全量工具 prompt eval（流式首 delta）----
print("\n=== qwen3-4b + 57 工具：流式首 delta TTFB ===")
body = {"model": "huihui_ai/qwen3-abliterated:4b",
        "messages": [{"role": "user", "content": STORY}],
        "tools": TOOLS, "stream": True, "max_tokens": 4096}
t0 = time.time()
try:
    rr = requests.post(f"{BASE}/chat/completions", json=body, timeout=(5, 180), stream=True)
    rr.raise_for_status()
    got = False
    for chunk in rr.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        for line in chunk.split("\n"):
            if line.strip().startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    delta = d.get("choices", [{}])[0].get("delta", {})
                    if any(delta.get(k) for k in ("reasoning", "reasoning_content", "content")):
                        print(f"  首个生成 delta 在 {time.time()-t0:.1f}s 后到达（≈prompt eval 时间），"
                              f"字段: {[k for k in ('reasoning','reasoning_content','content') if delta.get(k)]}")
                        got = True
                        break
                except json.JSONDecodeError:
                    pass
        if got:
            break
    rr.close()
    if not got:
        print(f"  {time.time()-t0:.1f}s 内无生成 delta")
except Exception as e:
    print(f"  ❌ {e}")

# ---- 5. qwen3 关思考实验 ----
print("\n=== qwen3-4b + chat_template_kwargs.enable_thinking=false ===")
body = {"model": "huihui_ai/qwen3-abliterated:4b",
        "messages": [{"role": "user", "content": "1+1等于几？直接回答"}],
        "stream": True, "max_tokens": 100,
        "chat_template_kwargs": {"enable_thinking": False}}
try:
    rr = requests.post(f"{BASE}/chat/completions", json=body, timeout=(5, 120), stream=True)
    rr.raise_for_status()
    fields = {}
    sample = ""
    t0 = time.time()
    for chunk in rr.iter_content(chunk_size=None, decode_unicode=True):
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        for line in chunk.split("\n"):
            if line.strip().startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    delta = d.get("choices", [{}])[0].get("delta", {})
                    for k in ("reasoning", "reasoning_content", "content"):
                        if delta.get(k):
                            fields[k] = fields.get(k, 0) + 1
                            if k == "content" and len(sample) < 80:
                                sample += delta[k]
                except json.JSONDecodeError:
                    pass
        if time.time() - t0 > 40:
            break
    rr.close()
    print(f"  字段统计: {fields}")
    print(f"  content 开头: {sample[:80]!r}")
    print(f"  → 思考{'已关闭 ✅' if not fields.get('reasoning') else '仍存在 ❌（该开关对 Ollama 无效）'}")
except Exception as e:
    print(f"  ❌ {e}")
