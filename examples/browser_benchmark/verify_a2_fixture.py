#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_a2_fixture.py — P2-7 A2 · fixture-only / 非 Agent 前置验证（本轮不跑 Agent 真任务）

目的：在"非聊天、纯离线动态页"上，客观确认 P2-6 frozen 的语义观察管道能独立工作：
  Anchor 定位 → Baseline → 触发查询 → Wait-for-Change(loading→delta) → Semantic Delta。
全程不用 Agent/LLM，只直接驱动 BrowserController 的语义方法 + 一个本地 mock 点击。

用法：
  python examples/browser_benchmark/verify_a2_fixture.py                 # 默认用 dynamic_anchor.html
  python examples/browser_benchmark/verify_a2_fixture.py --file dynamic.html   # 现状对照（预期锚失败）
  python examples/browser_benchmark/verify_a2_fixture.py --port 8713

输出硬断言（通过门槛）：
  anchor_ok=True；wait.state=COMPLETED；wait.delta_detected=True；
  evidence.source=semantic_delta；message_delta 含新增结果行（城市：北京 → 天气：晴）。
当前不支持锚的页面会如实报 anchor_lost（这是发现，不是粉饰）。
"""
import argparse
import http.server
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.browser_controller import BrowserController

DEMO_DIR = Path(__file__).resolve().parent


def _serve(base: Path, port: int):
    os.chdir(str(base))
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(base), **k)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _close(bc):
    try:
        bc.close()
    except Exception:
        pass


def check(bc, url, phase):
    print(f"\n===== [Phase {phase}] {url.split('?')[0].split('/')[-1]} =====")
    # A) Anchor 定位能力（现状诊断）
    disc = bc.discover_message_root(None)
    print("[anchor] discover_message_root:", {k: disc.get(k) for k in ("ok", "error", "reliable") if k in disc})
    st = bc.semantic_state()
    print("[anchor] semantic_state:", st)
    if st.get("error") == "anchor_lost" or not st.get("fingerprint"):
        print("[a2/ready] PRECONDITION_FAILED —— 该 fixture 无法建立语义锚（现状观察，非抹平）。")
        return {"ok": False, "reason": st.get("error", "no fingerprint"),
                "disc": disc.get("error", disc.get("reliable"))}

    # B) Baseline
    base = bc.semantic_baseline()
    print(f"[baseline] sem_block_count={base.get('sem_block_count')} "
          f"leaf_lines={base.get('leaf_lines')} fp={base.get('fingerprint')}")

    # C) 触发真实查询（本地 mock 点击，产生 loading→结果更新）
    bc._evaluate('document.getElementById("btn_query").click()')

    # D) Wait-for-Change（用 Phase C 状态机）—— 不传 baseline，走 pending？改为显式传 baseline 更受控
    states = []

    def trace(t): states.append(t["state"])

    wf = bc.wait_for_changes(baseline=base, timeout=15.0, min_stable_rounds=2, interval=0.5, trace=trace)
    ev = wf.get("evidence") or {}
    print(f"[wait] state={wf.get('state')} delta_detected={wf.get('delta_detected')} "
          f"elapsed_ms={wf.get('elapsed_ms')} loading={wf.get('loading')}")
    print(f"[wait] trace_states = {states}")
    print(f"[delta] message_delta = {wf.get('message_delta')!r}")
    print(f"[delta] evidence = {ev}")
    print(f"[delta] blocks = {wf.get('message_blocks')}")

    ok = (wf.get("state") == "COMPLETED"
          and wf.get("delta_detected")
          and ev.get("source") == "semantic_delta"
          and ev.get("verified")
          and "晴" in (wf.get("message_delta") or ""))
    print(f"[a2/ready] RESULT = {'PASS' if ok else 'FAIL'}")
    return {"ok": ok, "state": wf.get("state"), "delta": wf.get("message_delta")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="dynamic_anchor.html")
    ap.add_argument("--port", type=int, default=8713)
    ap.add_argument("--phase", default="B")   # B=改造版验证；A=现状诊断
    args = ap.parse_args()

    fh = DEMO_DIR / args.file
    if not fh.exists():
        print(f"[a2] 找不到 fixture: {fh}")
        return 2

    httpd = _serve(DEMO_DIR, args.port)
    url = f"http://127.0.0.1:{args.port}/{args.file}?bench={int(time.time()*1000)}"
    print(f"[a2] serve {url}")

    bc = BrowserController()
    try:
        ws = bc.launch(port=9223, headless=True)
        print("[a2] launched:", str(ws)[:110])
        bc.navigate(url)
        bc._evaluate('document.getElementById("btn_query") && 1')  # 等 DOM 就绪
        r = check(bc, url, args.phase)
    except Exception as e:
        print("[a2] EXCEPTION:", repr(e))
        r = {"ok": False, "reason": repr(e)[:120]}
    finally:
        _close(bc)
        try:
            httpd.shutdown()
        except Exception:
            pass

    print(f"\n[a2/ready] overall PASS={r.get('ok')}  ({r.get('reason', '')})")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())