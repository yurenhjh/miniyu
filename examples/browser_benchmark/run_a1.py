#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_a1.py — P2-7 Phase 2 · A1 Local Static 一次性 driver（不修改 Core / 不碰 P2-6）

流程：
  1. 在 127.0.0.1 起一个临时静态服务器，serve examples/browser_benchmark/
  2. 用项目真实 Agent（load_config()，读 config.yaml 的 LLM）跑 A1 任务
     ——find「查看更多」→ handle → click → targeted read 展开的第二段正文
  3. 用 core/benchmark.aggregate_run_log 聚合 run_log，打印 benchmark summary
  4. task_success 用目标文本的子串做**确定性**客观判定（不作 AI 语义判断）

A1 通过门槛（gtp）：find=1 / click=1 / read_targeted=1 / SoM=0 / selector_fallback=0 /
coordinate_click=0 / action_failures=0 / recovery_depth=0 / wait_*=0 /
task_success=true / verified_success=true / benchmark_valid=true
"""
import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.agent import Agent
from core.agent_config import load_config
from core.benchmark import aggregate_run_log, format_summary

DEMO_DIR = Path(__file__).resolve().parent
TARGET_TEXT_MARK = "第二段"      # 展开后第三段/第二段正文的稳定子串（确定性判定用）
TARGET_TEXT_MARK2 = "第三段（展开后可见）"


def _serve(base: Path, port: int) -> None:
    os.chdir(str(base))
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(base), **k)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _confirm_handler(pv):   # A1 无高危操作；一律放行避免阻塞
    return True


def main():
    if "--doc-fix-only" in sys.argv:
        return 0

    port = int(os.environ.get("A1_PORT", "8711"))
    url = f"http://127.0.0.1:{port}/static.html"

    httpd = _serve(DEMO_DIR, port)
    print(f"[a1] 本地静态服务起在 {url}")

    config = load_config()
    agent = Agent(config=config, confirm_handler=_confirm_handler)

    task = (
        f"打开本地 Benchmark 页面 {url}。"
        "找到『查看更多』按钮，用 browser_find 定位并取得其 Target Handle，"
        "然后用该 handle 执行 browser_click 展开，"
        "对展开后新增的第二段正文执行 targeted read（定向读取，不要整页读），"
        "最后把读到的完整第二段正文原样返回。"
    )

    print("[a1] 任务:", task, "\n")
    try:
        answer = agent.run(task)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass

    print("\n===== Agent 终答 =====")
    print(answer)

    run_log = getattr(agent, "_run_log", None)
    if not run_log or not os.path.exists(run_log):
        print("[a1] 未找到 run_log:", run_log)
        return 2

    # task_success：终答是否包含目标的确定性文本（客观判定，非 AI 语义判断）
    hit = bool(answer) and (TARGET_TEXT_MARK in answer or TARGET_TEXT_MARK2 in answer)
    s = aggregate_run_log(run_log, task_success=hit)

    print("\n" + "=" * 60)
    print(f"===== A1 Local Static Benchmark Summary（{os.path.basename(run_log)}） =====")
    print(f"  task_success        = {s['task_success']}   (客观文本命中: {hit})")
    print(f"  verified_success    = {s['verified_success']}")
    print(f"  benchmark_valid     = {s['benchmark_valid']}")
    print(f"  llm_calls           = {s['llm_calls']}")
    print(f"  run_total_tokens    = {s['run_total_tokens']}")
    print(f"  find / click / read_targeted = {s['find']} / {s['click']} / {s['read_targeted']}")
    print(f"  som_screenshot / selector_fallback / coordinate_click = "
          f"{s['som_screenshot']} / {s['selector_fallback']} / {s['coordinate_click']}")
    print(f"  action_failures     = {s['action_failures']}")
    print(f"  recovery_depth      = {s['recovery_depth']} (is_heuristic_v1={s['recovery_depth_is_heuristic_v1']})")
    print(f"  wait_completed/timeout/anchor_lost/loading_stuck = "
          f"{s['wait_completed']}/{s['wait_timeout']}/{s['wait_anchor_lost']}/{s['wait_loading_stuck']}")
    print(f"  verification_source / path = {s['verification_source']} / {s['verification_path']}")
    print(f"  run_log             = {run_log}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())