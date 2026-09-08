"""
probe_search_trigger.py — 实测：enable_search 开启后，模型什么情况下才真的去搜？

三类问题对照（均带 enable_search=true，真实 API）：
  K  纯知识题："用两句话解释什么是递归"（模型自己就会，理论上不该搜）
  T  时效题：  "今天杭州天气怎么样"（已知会搜）
  P  排除缓存：每个问题独立请求，测首字延迟与总延迟

检测手段：
  1. 响应 JSON 里的 search_info / 服务端搜索字段
  2. usage 里的额外字段
  3. content 里的引用痕迹（"参考" / [1] / http）
  4. 耗时对比（搜索要联网取数，延迟会显著变长）
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.agent_config import load_config

QUESTIONS = [
    ("K-纯知识", "用两句话解释什么是递归，不要搜索"),
    ("K2-纯知识(不加禁令)", "用两句话解释什么是冒泡排序"),
    ("T-时效", "今天杭州天气怎么样"),
    ("T2-时效", "最近人民币兑美元汇率大概多少"),
]

MARKERS = ("search_info", "参考", "[1]", "http://", "https://", "来源", "截至")


def run_once(cfg, question, enable_search):
    body = {
        "model": cfg["llm"]["model"],
        "messages": [
            {"role": "system", "content": "你是一个助手。"},
            {"role": "user", "content": question},
        ],
        "temperature": 0.7,
        "max_tokens": 512,
    }
    if enable_search:
        body["enable_search"] = True

    t0 = time.time()
    r = requests.post(
        f"{cfg['llm']['base_url'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {cfg['llm']['api_key']}"},
        json=body, timeout=120,
    )
    dt = time.time() - t0
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}", "latency": dt}

    data = r.json()
    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content") or ""
    top_extra = {k: v for k, v in data.items()
                 if k not in ("choices", "usage", "model", "id", "created", "object")}
    usage = data.get("usage", {})
    usage_extra = {k: v for k, v in usage.items()
                   if k not in ("prompt_tokens", "completion_tokens", "total_tokens")}
    msg_extra = {k: v for k, v in choice.get("message", {}).items()
                 if k not in ("role", "content", "tool_calls")}
    hit = [m for m in MARKERS if m in content]
    return {
        "latency": dt,
        "search_info": top_extra or None,
        "msg_extra": msg_extra or None,
        "usage_extra": usage_extra or None,
        "usage_full": usage,
        "markers": hit,
        "preview": content[:200],
    }


def main():
    cfg = load_config()
    print(f"模型: {cfg['llm']['model']}\n")

    # 对照组：知识题关掉 enable_search（纯生成延迟基线）
    base = run_once(cfg, "用两句话解释什么是递归", enable_search=False)
    if "error" in base:
        print(f"[基线失败] {base['error']}")
        return
    print(f"基线（知识题·搜索关）: 延迟 {base['latency']:.1f}s  引用痕迹 {base['markers'] or '无'}")
    print(f"  完整 usage: {json.dumps(base['usage_full'], ensure_ascii=False)}")

    for label, q in QUESTIONS:
        if not label.startswith("K"):
            continue  # 本轮只验证固定开销，跳过时效题（已有数据，且产生搜索费用）
        r = run_once(cfg, q, enable_search=True)
        if "error" in r:
            print(f"{label}: ❌ {r['error']}")
            continue
        print(f"\n{label}: 「{q}」")
        print(f"  延迟 {r['latency']:.1f}s  引用痕迹 {r['markers'] or '无'}")
        print(f"  完整 usage: {json.dumps(r['usage_full'], ensure_ascii=False)}")
        print(f"  预览: {r['preview'][:120]}")


if __name__ == "__main__":
    main()
