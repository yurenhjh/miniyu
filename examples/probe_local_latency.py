"""
probe_local_latency.py — 本地 Ollama 模型延迟诊断（复现"写故事超时"问题）

测量（每个模型 × 两种负载）：
  A. 真实负载：agent 的 system prompt + 62 个工具 schema + "帮我写一段关于兔子和乌龟的故事"
     —— 完整还原 agent.run 实际发出的请求
  B. 裸负载：只有 user 消息，无 system 无 tools（对照组）

采集指标：
  - 首字延迟 TTFB（≈ prompt eval 时间）
  - 生成速度 tok/s（流式采 12 秒）
  - 思考行为：qwen3 是否先输出 <think> 或 reasoning_content 字段
  - 估算完整生成时间是否超过 120s 超时
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.agent import SYSTEM_PROMPT
from core.os_service_api import OSServiceAPI

STORY = "帮我写一段关于兔子和乌龟的故事"
SAMPLE_SECONDS = 12


def probe(base_url, model, payload_label, messages, tools):
    body = {"model": model, "messages": messages, "stream": True, "max_tokens": 4096}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    t0 = time.time()
    try:
        r = requests.post(f"{base_url}/chat/completions", json=body,
                          timeout=(5, 120), stream=True)
        r.raise_for_status()
    except Exception as e:
        print(f"    ❌ 请求失败: {e}")
        return
    ttfb = None
    n_chunks = 0
    chars = 0
    first_content = ""
    has_reasoning_field = False
    saw_think_tag = False
    tool_calls = {}
    t_start = None
    for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
        now = time.time()
        if t_start is None:
            t_start = now
        if now - t_start > SAMPLE_SECONDS:
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
            content = delta.get("content") or ""
            if delta.get("reasoning_content"):
                has_reasoning_field = True
            if "<think>" in content:
                saw_think_tag = True
            if content:
                if ttfb is None:
                    ttfb = now - t0
                n_chunks += 1
                chars += len(content)
                if len(first_content) < 150:
                    first_content += content
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                tool_calls.setdefault(idx, "")
                fn = tc.get("function", {})
                tool_calls[idx] += fn.get("arguments", "") or fn.get("name", "")
    r.close()
    dur = time.time() - t_start
    tps = n_chunks / dur if dur > 0 else 0
    print(f"    TTFB {ttfb if ttfb else -1:>6.1f}s | 速度 {tps:5.1f} tok/s | "
          f"采样 {n_chunks} tokens/{SAMPLE_SECONDS}s | reasoning字段={'有' if has_reasoning_field else '无'} "
          f"| <think>标签={'有' if saw_think_tag else '无'} | 工具调用={list(tool_calls.values()) if tool_calls else '无'}")
    print(f"    开头: {first_content[:120]!r}")
    # 估算：一段 800 token 的故事（含思考）需要多久
    if tps > 0:
        est = 800 / tps
        print(f"    → 生成 800 token 约需 {est:.0f}s（120s 超时{'会触发 ⚠️' if est > 100 else '内 ✅'}）")


def main():
    base_url = "http://localhost:11434/v1"
    api = OSServiceAPI()
    tools = api.list_tools_mcp()
    tools = [{"type": "function", "function": t} for t in tools]
    print(f"工具 schema 数: {len(tools)}")

    # 工具 schema 体积（prompt 负担）
    tools_json = json.dumps(tools, ensure_ascii=False)
    print(f"工具 schema 总体积: {len(tools_json)} 字符 ≈ {len(tools_json)//2} tokens\n")

    # GPU/CPU 状态
    try:
        ps = requests.get("http://localhost:11434/api/ps", timeout=5).json()
        for m in ps.get("models", []):
            print(f"已加载: {m['name']} | GPU: {m.get('size_vram', 0) > 0} | "
                  f"vram {m.get('size_vram', 0)/1e9:.1f}G / total {m.get('size', 0)/1e9:.1f}G")
    except Exception as e:
        print(f"ps 查询失败: {e}")
    print()

    for model in ["huihui_ai/qwen3-abliterated:4b",
                  "richardyoung/qwen2.5-7b-instruct-abliterated:latest"]:
        print(f"=== {model} ===")
        print("  [A] 真实负载（system + 62 工具）")
        probe(base_url, model, "A",
              [{"role": "system", "content": SYSTEM_PROMPT},
               {"role": "user", "content": STORY}], tools)
        print("  [B] 裸负载（仅 user）")
        probe(base_url, model, "B",
              [{"role": "user", "content": STORY}], None)
        print()


if __name__ == "__main__":
    main()
