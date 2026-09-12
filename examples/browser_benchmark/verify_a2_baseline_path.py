#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_a2_baseline_path.py — P2-7 A2 · 只读架构核查：click→wait_for_change() 的合法 baseline 入口
本轮：不跑 Agent / 不改 P2-6 Core / 不执行 C/D。只实证一条链路：

 runner(共享 api.registry.browser) 在 click 前调冻结的 P2-6 既有方法 capture_wait_baseline()
 → 同一实例上 Agent 将走的 find/type/click 触发（这里用确定性 find→click 模拟）
 → browser_wait_for_change(空参) → 消费 pending → COMPLETED（而非 NO_PENDING_WAIT_BASELINE）

关键：capture_wait_baseline / wait_for_changes 都是已冻结方法，此处仅"调用"，不做任何修改。
click 不触发 press_enter 捕获，因此 pending 只能来自 runner 的显式建立 —— 这回答了架构问题。
"""
import http.server
import os
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.os_service_api import OSServiceAPI

DEMO_DIR = Path(__file__).resolve().parent


def _serve(base: Path, port: int):
    os.chdir(str(base))
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(base), **k)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    httpd = _serve(DEMO_DIR, 8714)
    url = f"http://127.0.0.1:8714/dynamic.html?bench={int(time.time()*1000)}"
    print(f"[check] serve {url}")

    api = OSServiceAPI()
    bc = api.registry.browser          # 与 Agent 工具共享的同一实例（runner 侧入口）
    try:
        # 用临时档案 + headless 启动（避免 config 持久 profile 锁残留；仍是共享实例）
        prof = tempfile.mkdtemp(prefix="a2check_")
        bc.launch(port=9224, headless=True, user_data_dir=prof)
        r = api.execute_tool("browser_navigate", {"url": url})   # 走工具层，同一 browser
        print("[check] navigate:", str(r)[:90])
        bc._evaluate('document.getElementById("btn_query") && 1')   # 等 DOM

        # ① runner 侧在 click 前建立 observation baseline（冻结方法，不改 Core）
        base = bc.capture_wait_baseline()
        print("[check] capture_wait_baseline ->", base)
        pending = getattr(bc, "_pending_wait_baseline", None)
        print("[check] pending bound to shared instance:", bool(pending),
              "keys=", list(pending.keys()) if pending else None)
        if not base.get("pending"):
            print("[check] FAIL: pending 未建立"); return 1

        # ② 确定性执行 Agent 将走的 click 触发（不经过 press_enter，故不会自己捕获 baseline）
        r = api.execute_tool("browser_click", {"selector": "#btn_query"})
        print("[check] click(查询) ->", (r.get("success") if isinstance(r, dict) else r))

        # ③ Agent 将走的空参 browser_wait_for_change() —— 关键：应消费 pending，而非 NO_PENDING
        wf = api.execute_tool("browser_wait_for_change", {})
        st = wf.get("result") if isinstance(wf, dict) else wf
        print("[check] wait_for_change(空参) ->", {k: st.get(k) for k in
              ("state", "delta_detected", "loading", "elapsed_ms")} if isinstance(st, dict) else st)
        if isinstance(st, dict):
            print("[check] message_delta =", repr(st.get("message_delta")))
            print("[check] evidence     =", st.get("evidence"))

        ok = isinstance(st, dict) and st.get("state") == "COMPLETED" and st.get("delta_detected") \
             and (st.get("evidence") or {}).get("source") == "semantic_delta"
        print(f"[check] 结论: click→wait_for_change(空参) 消费 runner 预置 pending = "
              f"{'PASS ✅（无需改 Core）' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        try:
            bc.close()
        except Exception:
            pass
        try:
            httpd.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())