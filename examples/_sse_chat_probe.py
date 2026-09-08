"""_sse_chat_probe.py — 发一条消息走完整 SSE 流，用于验证会话落盘（临时诊断脚本）"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5000"
msg = sys.argv[1] if len(sys.argv) > 1 else "你好，用一句话介绍你自己。"

body = json.dumps({"message": msg}).encode()
req = urllib.request.Request(f"{BASE}/chat", data=body,
                             headers={"Content-Type": "application/json"})
task_id = json.loads(urllib.request.urlopen(req, timeout=10).read())["task_id"]
print(f"task: {task_id}")

t0 = time.time()
first_token = None
n_reasoning = n_token = n_tool = 0
final = ""
with urllib.request.urlopen(f"{BASE}/task/{task_id}/events", timeout=300) as r:
    for line in r:
        line = line.decode("utf-8").strip()
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        t = ev.get("type")
        if t == "reasoning":
            n_reasoning += 1
            if first_token is None:
                first_token = time.time() - t0
        elif t == "token":
            n_token += 1
            if first_token is None:
                first_token = time.time() - t0
        elif t == "tool_call":
            n_tool += 1
            print(f"  [tool_call] {ev.get('tool')} {json.dumps(ev.get('arguments'), ensure_ascii=False)[:80]}")
        elif t in ("done", "error"):
            final = ev.get("result", "")
            print(f"[{t}] 用时 {time.time() - t0:.1f}s | reasoning块={n_reasoning} token块={n_token} 工具={n_tool}")
            if first_token:
                print(f"首token: {first_token:.1f}s")
            print(f"回复: {final[:300]}")
            break
