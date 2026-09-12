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

A1 通过门槛（gtp，find 次数重点看关键路径而非固定值）：
  task_success=true / verified_success=true / benchmark_valid=true
  click(handle)=1 / read_ok_targeted=1
  action_failures=0 / recovery_depth=0 / selector_fallback=0 / coordinate_click=0
  verification_path=read_targeted
关键成功路径：fixture ready → find(查看更多)→e1 → click(e1) → find(role=region,text=第二段)→e2
  → read_text(target=e2) → structured_read。

driver 责任（P2-7 A1 runner）：在 agent.run 之前，用 agent.api.execute_tool 驱动【同一个浏览器】
  完成 browser_launch → browser_navigate(cache-bust url) → find(role=region) 就绪校验；
  任一失败直接 abort，绝不带旧/缺 fixture 跑 Agent。该 readiness 不计入 benchmark 指标。
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
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


def _close_browser(api):
    """每次 run 结束必须关掉浏览器：持久化档案(user_data_dir)下残留实例会锁住
    profile/9222 端口，导致下一次 open 加载不出页面（旧/空 DOM）。best-effort。"""
    try:
        api.execute_tool("browser_close", {})
        print("[a1] 浏览器已关闭（防止下次加载不出页面）")
    except Exception:
        pass


def _call(api, name, params, label):
    r = api.execute_tool(name, params or {})
    if not isinstance(r, dict) or not r.get("success"):
        raise RuntimeError(f"{label} 未成功: {r}")
    return r.get("result")


def _fixture_ready(api, url: str) -> dict:
    """driver 级 fixture readiness 前置校验（不进 benchmark 指标，只保实验对象正确）。

    保证：浏览器已启动、已 navigate 到带 cache-bust 的最新 static.html、
    新版 role=region fixture 可被 find 寻址。任一失败 → ready=False，直接 abort。
    """
    print("[a1/ready] 启动浏览器 …")
    try:
        r = _call(api, "browser_launch", {}, "browser_launch")
        print("[a1/ready] browser_launch:", str(r)[:120])
    except Exception as e:
        return {"ready": False, "reason": f"browser_launch 失败: {e}"}

    print(f"[a1/ready] 显式 navigate => {url} …")
    try:
        r = _call(api, "browser_navigate", {"url": url}, "browser_navigate")
        print("[a1/ready] browse_navigate:", str(r)[:120])
    except Exception as e:
        return {"ready": False, "reason": f"browser_navigate 失败: {e}"}

    # 校验新版 fixture：role=region 可被 find 寻址（这就是「正确观察对象」的客观信号）
    try:
        hits = _call(api, "browser_find", {"role": "region"}, "browser_find(role=region)")
        hits = [x for x in (hits or []) if isinstance(x, dict)]
        if not hits:
            return {"ready": False,
                    "reason": "find(role=region)=[]：浏览器未加载新版 static.html（旧/缺 fixture），abort"}
        refs = [(x.get("handle"), x.get("ref")) for x in hits]
        print("[a1/ready] find(role=region) 命中:", refs)
    except Exception as e:
        return {"ready": False, "reason": f"fixture 校验失败: {e}"}

    return {"ready": True, "handles": refs}


def main():
    if "--doc-fix-only" in sys.argv:
        return 0

    port = int(os.environ.get("A1_PORT", "8711"))
    # 最小 cache-busting：每轮唯一地址，避免吃到旧 static.html 缓存（不改页面逻辑）。
    url = f"http://127.0.0.1:{port}/static.html?bench={int(time.time() * 1000)}"

    httpd = _serve(DEMO_DIR, port)
    print(f"[a1] 本地静态服务起在 {url}")

    config = load_config()
    agent = Agent(config=config, confirm_handler=_confirm_handler)

    # 前置：确保本次会话真正加载当前 fixture 才开始 benchmark。
    # 只有 navigate 成功 + role=region 可寻址才算 ready；否则直接 abort，绝不带错对象跑 Agent。
    ready = _fixture_ready(agent.api, url)
    if not ready["ready"]:
        print("\n[a1] Fixture readiness FAILED —— abort（不执行 Agent task）：")
        print("     ", ready["reason"])
        try:
            httpd.shutdown()
        except Exception:
            pass
        _close_browser(agent.api)
        return 3
    print("[a1/ready] fixture ready，仅在该对象上启动 benchmark task\n")

    if "--readiness-only" in sys.argv:
        print("[a1] readiness-only：跳过 Agent task（不重跑 A1），返回就绪结果。")
        print("[a1] region handles:", ready["handles"])
        try:
            httpd.shutdown()
        except Exception:
            pass
        _close_browser(agent.api)
        return 0

    task = (
        f"本地 Benchmark 页面已就绪（当前已加载，请勿再调用 browser_launch / browser_navigate）：{url}。"
        "先找到『查看更多』按钮，用 browser_find 定位并取得其 Target Handle，"
        "然后用该 handle 执行 browser_click 展开隐藏内容；"
        "展开后，再用 browser_find 定位『展开后的内容区域』——它是语义容器（role=region，"
        "文本包含『第二段』），取得其 Target Handle，"
        "用该 handle 对该区域执行 targeted read（定向读取，不要整页读，也不要自行猜测 CSS selector），"
        "最后把读到的内容区域里的第二段正文原样返回。"
    )

    print("[a1] 任务:", task, "\n")
    try:
        answer = agent.run(task)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        _close_browser(agent.api)

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