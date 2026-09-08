"""
e2e_stop_encoding_live.py：真实环境验证「乱码修复 + 停止生成」

前置：Web 服务（5000）与 Ollama（11434）都在运行。
验证两项 2026-09-07 修复：
  A) UTF-8 乱码：Ollama 流式响应头不带 charset，旧实现被 requests 按
     ISO-8859-1 解码，中文全变 'ä½ å¥½' 式 mojibake。修复后 SSE token
     必须是正常中文。
  B) 停止生成：流式中 POST /task/<id>/stop → agent 在 chunk 间隙终止，
     done 事件携带「部分文本 + ⏹ 已手动停止」标记，且停止请求秒级生效。

用法：
  python examples/e2e_stop_encoding_live.py [模型名]
  默认：huihui_ai/qwen3-abliterated:4b
"""

import json
import sys
import time
import urllib.request

BASE = "http://localhost:5000"

# UTF-8 按 Latin-1 误解码的特征片段（出现即 mojibake）
MOJIBAKE_MARKS = ("ä½", "å¥", "æ°", "è¯", "ç", "ã")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    mark = "✅" if cond else "❌"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark} {name}" + (f"  [{detail}]" if detail else ""))
    return cond


def post(path, payload=None, timeout=120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def sse_events(task_id, timeout=900):
    """逐条 yield /task/<id>/events 的 data 事件（跳过心跳注释行）"""
    req = urllib.request.Request(f"{BASE}/task/{task_id}/events")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if line.startswith("data: "):
                yield json.loads(line[6:])


def has_chinese(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def run(model):
    print(f"\n========== 模型：{model} ==========")

    # 0) 切换模型：本地 Ollama 模型走 /switch-fallback-model（自动 force_local），
    #    在线模型走 /switch-model（带连通性 probe）
    with urllib.request.urlopen(BASE + "/api/fallback-models", timeout=30) as r:
        local_models = json.loads(r.read().decode("utf-8")).get("models", [])
    if model in local_models:
        r = post("/switch-fallback-model", {"model": model})
    else:
        r = post("/switch-model", {"model": model})
    print(f"切换：{'ok' if r.get('ok') else r.get('message')}")
    if not r.get("ok"):
        return False
    time.sleep(1)

    # ---------- A) 乱码修复 ----------
    print("\n[A] 乱码修复验证（正常中文流式输出）")
    t0 = time.time()
    task = post("/chat", {"message": "你好，请用两三句话介绍一下你自己。"})
    text, reasoning, first_tok_at, first_reason_at = "", "", None, None
    final = None
    for ev in sse_events(task["task_id"]):
        t = ev.get("type")
        if t == "token":
            if first_tok_at is None:
                first_tok_at = time.time() - t0
            text += ev.get("delta", "")
        elif t == "reasoning":
            if first_reason_at is None:
                first_reason_at = time.time() - t0
            reasoning += ev.get("delta", "")
        elif t in ("done", "error"):
            final = ev
            break
    total = time.time() - t0

    ok = final is not None and final.get("type") == "done"
    check("任务完成（done）", ok,
          final.get("result", "")[:40] if final else "无终态事件")
    if ok:
        result = final.get("result", "")
        check("正文含中文", has_chinese(result), f"{len(result)} 字")
        check("无 mojibake 特征串", not any(m in result for m in MOJIBAKE_MARKS))
        check("SSE token 无 mojibake", not any(m in text for m in MOJIBAKE_MARKS))
        if reasoning:
            check("思考流无 mojibake", not any(m in reasoning for m in MOJIBAKE_MARKS),
                  f"reasoning {len(reasoning)} 字")
        first = first_tok_at if first_tok_at is not None else first_reason_at
        check("流式首事件 < 120s", first is not None and first < 120,
              f"{first:.1f}s" if first else "无")
        print(f"  ⏱ 首事件 {first:.1f}s | 总耗时 {total:.1f}s | "
              f"正文 {len(result)} 字"
              + (f" | 思考 {len(reasoning)} 字" if reasoning else ""))
        print(f"  📄 正文片段：{result[:60]}")

    # ---------- B) 停止生成 ----------
    print("\n[B] 停止生成验证（流式打断）")
    t0 = time.time()
    task = post("/chat", {"message": "请写一篇 800 字的春天散文，直接开始写。"})
    tid = task["task_id"]
    text, n_tok, stop_at, done_at, final = "", 0, None, None, None
    for ev in sse_events(tid):
        t = ev.get("type")
        if t == "token":
            n_tok += 1
            text += ev.get("delta", "")
            if n_tok == 8:  # 流出 8 个正文 token 后请求停止
                stop_at = time.time() - t0
                s = post(f"/task/{tid}/stop")
                check("stop 端点受理", s.get("ok") is True, s.get("message", ""))
        elif t in ("done", "error"):
            done_at = time.time() - t0
            final = ev
            break

    ok = final is not None and final.get("type") == "done"
    check("停止后收到 done 终态", ok)
    if ok:
        result = final.get("result", "")
        check("终态含「已手动停止」标记", "已手动停止" in result)
        check("保留了已流出的部分文本", has_chinese(result) and len(result) > len("⏹"))
        check("停止秒级生效（< 5s）",
              stop_at is not None and done_at is not None and done_at - stop_at < 5,
              f"{(done_at - stop_at):.1f}s" if stop_at and done_at else "无时间戳")
        check("被打断（未跑完全文）", "800" not in result or len(result) < 700)
        print(f"  ⏱ 停止请求 @ {stop_at:.1f}s → 终态 @ {done_at:.1f}s | "
              f"截留 {n_tok} 个 token")
        print(f"  📄 终态片段：{result[:80]}")

    return True


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "huihui_ai/qwen3-abliterated:4b"
    try:
        run(model)
    except Exception as e:
        FAIL += 1
        print(f"❌ 异常：{e}")
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
