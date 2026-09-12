#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_d.py — P2-7 D：Public Web Smoke（唯一一次 Agent 实测，不改 Core / Prompt / find scope / 不改 System Prompt）

D=Smoke：只把 task_success/verified_success/benchmark_valid 作为 PASS 门槛，
recovery_depth/token/fallback 等仅作诊断指标。失败不修不重跑，按 external/site-specific/own-capability 归因。

真实链路（preflight 已实证，click 后须重新 find，旧 handle 会 stale）：
  find(role=link, text=Examples) → handle(e1) → click(e1)
  → 重新 find(role=link, text=Examples) → handle(e2) → read_text(target=e2)
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.agent import Agent
from core.agent_config import load_config
from core.benchmark import aggregate_run_log

URL = "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/button"

# D oracle（P2-8 新标准）：确定性最小归一化后，终答必须同时命中
#   [1] 标题锚「Examples」小节
#   [2] 该小节正文首句/目标正文（related-content read 实测返回 "Creating a basic button"）
# 杜绝 P2-7 只命中 "Examples" 就判 true 的假阳性。
_MD_EMPH = [("**", ""), ("*", ""), ("__", ""), ("_", "")]
_REQUIRED = ["Examples", "basic button"]


def _normalize(t):
    t = t or ""
    for a, b in _MD_EMPH:
        t = t.replace(a, b)
    return " ".join(t.split())


def _oracle_hit(ans):
    n = _normalize(ans)
    return all(m in n for m in _REQUIRED)


def _confirm_handler(pv):
    return True


def _close_browser(api):
    try:
        api.execute_tool("browser_close", {})
        print("[d] 浏览器已关闭")
    except Exception:
        pass


def _call(api, name, params, label):
    r = api.execute_tool(name, params or {})
    if not isinstance(r, dict) or not r.get("success"):
        raise RuntimeError(f"{label} 未成功: {r}")
    return r.get("result")


def _d_ready(api):
    """driver readiness：启动 → navigate 到 MDN → find(Examples) 非空。失败 abort（多为 external）。"""
    print("[d/ready] browser_launch …")
    try:
        _call(api, "browser_launch", {}, "browser_launch")
    except Exception as e:
        return {"ready": False, "reason": f"browser_launch 失败: {e}"}
    print(f"[d/ready] navigate => {URL} …")
    try:
        r = _call(api, "browser_navigate", {"url": URL}, "browser_navigate")
        print("[d/ready] browser_navigate:", str((r or {}).get("url") or "")[:120])
    except Exception as e:
        return {"ready": False, "reason": f"browser_navigate 失败: {e}"}
    for _ in range(60):
        try:
            hits = _call(api, "browser_find", {"role": "link", "text": "Examples"}, "browser_find(Examples)")
            hits = [x for x in (hits or []) if isinstance(x, dict)]
            if hits:
                print(f"[d/ready] find(role=link,text=Examples) 命中 {len(hits)} → fixture ready")
                return {"ready": True, "n": len(hits)}
        except Exception:
            pass
        time.sleep(0.5)
    print(f"[d/ready] find(role=link,text=Examples)=空：检索 {URL} 未就绪/结构异常（external 嫌疑）")
    return {"ready": False, "reason": "MDN 页拉开后未找到 Examples 链接（external/site-specific）"}


def _task_text():
    return (
        f"MDN 文档页已就绪（已打开，请勿再 browser_launch / browser_navigate）：{URL}。"
        "这是 HTML 元素 `<button>` 的参考文档页。任务：\n"
        "1) 先用 browser_find 定位目录或正文里文本为『Examples』的链接（页内锚点 #examples_2），"
        "取得其 Target Handle，用 browser_click(target=<handle>) 点击跳转；\n"
        "2) 点击跳转**之后**，不要复用跳转前的 handle（它会失效），请用 browser_find 重新定位『Examples』小节标题，"
        "取得新 Target Handle，用 browser_read_text(target=<新handle>) 定向读取该小节标题与正文首句；\n"
        "3) 把 read_text 读到的标题与正文首句原样返回（不要整页读取、不要自猜 selector）。"
    )


def main():
    config = load_config()
    agent = Agent(config=config, confirm_handler=_confirm_handler)
    api = agent.api
    try:
        ready = _d_ready(api)
        if not ready["ready"]:
            print(f"\n[d] 页面 readiness FAILED —— abort（不执行 Agent）：\n     {ready['reason']}")
            return 3
        print("[d/ready] fixture ready（MDN 已加载、Examples 链接可选址）\n")

        task = _task_text()
        print("[d] 任务:", task, "\n")
        answer = agent.run(task)
        print("\n===== Agent 终答 =====")
        print(answer or "(空)")
    except Exception as e:
        print("[d] EXCEPTION:", repr(e)[:300])
        return 2
    finally:
        _close_browser(api)
    return _summarize(agent, answer)


def _summarize(agent, answer):
    run_log = getattr(agent, "_run_log", None)
    if not run_log or not os.path.exists(run_log):
        print("[d] 未找到 run_log:", run_log)
        return 2
    hit = _oracle_hit(answer)
    s = aggregate_run_log(run_log, task_success=hit)

    print("\n" + "=" * 60)
    print(f"===== D Public Web Smoke Summary（{os.path.basename(run_log)}） =====")
    print(f"  task_success        = {s['task_success']}   (客观文本命中: {hit})")
    print(f"  verified_success    = {s['verified_success']}")
    print(f"  benchmark_valid     = {s['benchmark_valid']}")
    print(f"  llm_calls           = {s['llm_calls']}")
    print(f"  run_total_tokens    = {s['run_total_tokens']}")
    print(f"  image_tokens        = {s.get('image_tokens')}")
    print(f"  find / click / read_targeted = {s['find']} / {s['click']} / {s['read_targeted']}")
    print(f"  read_ok_targeted    = {s['read_ok_targeted']}")
    print(f"  som_screenshot / selector_fallback / coordinate_click = "
          f"{s['som_screenshot']} / {s['selector_fallback']} / {s['coordinate_click']}")
    print(f"  action_failures     = {s['action_failures']}")
    print(f"  recovery_depth      = {s['recovery_depth']} (is_heuristic_v1={s['recovery_depth_is_heuristic_v1']})")
    print(f"  wait completed/timeout/anchor_lost/loading_stuck = "
          f"{s['wait_completed']}/{s['wait_timeout']}/{s['wait_anchor_lost']}/{s['wait_loading_stuck']}")
    print(f"  verification_source / path = {s['verification_source']} / {s['verification_path']}")
    print(f"  run_log             = {run_log}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())