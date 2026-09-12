#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_a2.py — P2-7 Phase 2 · A2 Local Dynamic 一次性 driver（不修改 Core / 不碰 P2-6 frozen chain）

目标：证明 Anchor / Baseline / Wait-for-Change / Semantic Delta 是通用观测原语，
      在【非聊天、纯离线动态页】上独立成立（脱离豆包聊天语义）。

driver 责任边界（对照 A1 经验，严守"实验前置 vs 任务执行"分层）：
  run_a2.py                                     ← driver：只管实验前置条件
    ├─ launch
    ├─ navigate(dynamic.html?bench=<unique>)    ← cache-bust，防旧 DOM
    ├─ fixture readiness                        ← anchor 可发现 + baseline 成功捕获 + sem_block_count>0 + URL 正确
    ├─ capture_wait_baseline()                  ← driver 内部调用冻结的 P2-6 方法（共享实例），
    │                                              为 Agent 的 click→wait_for_change() 预置 pending baseline
    │                                              这不是给 Agent 看的工具
    └─ Agent 真任务                              ← Agent 只负责执行与结构化观测
         find(input)->handle->type("北京")
         find(查询)->handle->click()
         browser_wait_for_change()               ← 空参，消费 runner 预置 pending → COMPLETED
         （用返回的 message_delta 作结构化结果）

不做的"保险措施"（避免污染实验）：
  ❌ Agent 失败后自动 whole_page 兜底
  ❌ runner 主动 read 全页帮 Agent
  ❌ click 后 driver 再调一次 wait
  ❌ 失败后自动重建 baseline
  ❌ 给 Agent 任何 benchmark 专用工具 / A2 特供工具

A2 正式门槛（冻结，见 docs/P2-7-跨页面-Benchmark-设计.md §13.2）：
  task_success=true · verified_success=true · benchmark_valid=true
  delta_detected=true · wait_completed=true
  action_failures=0 · recovery_depth=0
  selector_fallback=0 · coordinate_click=0 · SoM=0
  wait_timeout=0 · wait_anchor_lost=0 · wait_loading_stuck=0
  verification_path ∈ { wait_delta, read_targeted }    # whole_page 不得作为 PASS 依据
"""
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.agent import Agent
from core.agent_config import load_config
from core.benchmark import aggregate_run_log

DEMO_DIR = Path(__file__).resolve().parent

# task_success 的确定性客观 oracle（仅 benchmark 判定层，不因表达格式误杀/放宽）
# 原则：① 只做确定性 Markdown 归一化（剥离强调分隔符），不模糊语义、不“只找晴”。
#       ② 内容判定与表达格式分离：归一化后再做精确子串匹配。
#       ③ 要求三要素全命中——同时挡住“北京北京/天气未知”与“只说晴”这类假成功。
_MD_EMPH_SPANS = [("**", ""), ("*", ""), ("__", ""), ("_", "")]
_REQUIRED_MARKS = ["城市：北京", "天气：晴", "温度：26℃"]


def _normalize_answer(text: str) -> str:
    """确定性、最小化归一化：剥离 Markdown 粗体/斜体/下划线强调，折叠空白。不做语义匹配。"""
    text = text or ""
    for a, b in _MD_EMPH_SPANS:
        text = text.replace(a, b)
    return " ".join(text.split())


def _oracle_hit_answer(answer) -> bool:
    """旧口径：直接对 Agent 终答文本做三要素子串判定。
    P2-8 判定：A2 task_success 以 wait_delta.message_delta 为准（见本模块 docstring），
    `verified_success` 由聚合器的 structured_read(wait_delta) 保证。此函数仅作终答辅助记录。"""
    norm = _normalize_answer(answer)
    return all(m in norm for m in _REQUIRED_MARKS)


def extract_wait_delta(run_log: str) -> str:
    """从 run_log 提取 browser_wait_for_change 返回的 message_delta（确定性）。

    被测动态原语直接产生的结构化证据 —— A2 task_success 应判它，而非 Agent 的排版。
    未命中 wait 或 delta 为空 → 返回 ""。

    注意：长 message_blocks 会让 action.result 在日志层被截断为不完整 JSON，
    故对 str 形式回退到正则头部标记做稳健提取，不做整体 json.loads。
    """
    delta = ""
    re_delta = re.compile(r'"message_delta"\s*:\s*"((?:\\.|[^"\\])*)"')
    with open(run_log, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if row.get("kind") != "action":
                continue
            if str(row.get("tool") or "") != "browser_wait_for_change":
                continue
            if not bool(row.get("ok")):
                continue
            res = row.get("result")
            if isinstance(res, str):
                ok = re.search(r'"delta_detected"\s*:\s*true', res)
                if not ok:
                    continue
                m = re_delta.search(res)
                if m:
                    d = m.group(1)
                    if d.strip():
                        delta = d
            elif isinstance(res, dict):
                if not res.get("delta_detected"):
                    continue
                d = res.get("message_delta") or res.get("delta") or ""
                if isinstance(d, str) and d.strip():
                    delta = d
    return delta


def _oracle_task_success(run_log: str) -> tuple:
    """P2-8 A2 oracle（方案 1）：task_success 绑定 wait_delta.message_delta 三要素判定。

    返回 (task_success, delta_ok, hit_keys)。不改被测能力，仅判定层口径统一：
      1. message_delta 三要素全部命中 → task_success=True（不得因空/格式宽松误判）；
      2. 否则以终答三要素兜底（保留旧路径可见性）；
      3. 两者皆空/皆缺 → False。
    """
    delta = extract_wait_delta(run_log)
    norm_delta = _normalize_answer(delta)
    delta_ok = all(m in norm_delta for m in _REQUIRED_MARKS)
    if delta_ok:
        return True, True, list(_REQUIRED_MARKS)
    # 兜底：终答（旧路径，供回归对比）
    return False, False, [m for m in _REQUIRED_MARKS if m not in norm_delta]


def _serve(base: Path, port: int) -> None:
    os.chdir(str(base))
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(base), **k)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _confirm_handler(pv):   # A2 无高危操作；一律放行避免阻塞
    return True


def _close_browser(api):
    """run 结束必关浏览器，杜绝 A1 的 stale browser（锁 profile/9222 → 旧 DOM）复发。best-effort。"""
    try:
        api.execute_tool("browser_close", {})
        print("[a2] 浏览器已关闭（防止下次加载不出页面）")
    except Exception:
        pass


def _call(api, name, params, label):
    r = api.execute_tool(name, params or {})
    if not isinstance(r, dict) or not r.get("success"):
        raise RuntimeError(f"{label} 未成功: {r}")
    return r.get("result")


def _a2_ready(api, url: str) -> dict:
    """driver 级 fixture readiness（不计入 benchmark 指标，只保证实验对象正确）。

    保证：浏览器已启动、已 navigate 到带 cache-bust 的最新 dynamic.html、
    anchor 可发现、baseline 成功捕获、sem_block_count>0、URL 正确。任一失败 → ready=False。
    其中 capture_wait_baseline() 是【driver 内部】预置 —— Agent 不可见，也不作为 Agent 工具。
    """
    print("[a2/ready] 启动浏览器 …")
    try:
        r = _call(api, "browser_launch", {}, "browser_launch")
        print("[a2/ready] browser_launch:", str(r)[:120])
    except Exception as e:
        return {"ready": False, "reason": f"browser_launch 失败: {e}"}

    print(f"[a2/ready] 显式 navigate => {url} …")
    try:
        r = _call(api, "browser_navigate", {"url": url}, "browser_navigate")
        nav_url = str((r or {}).get("url") or "")
        print("[a2/ready] browser_navigate:", nav_url[:140])
    except Exception as e:
        return {"ready": False, "reason": f"browser_navigate 失败: {e}"}
    if "dynamic.html?" not in nav_url:
        return {"ready": False, "reason": f"URL 非预期 dynamic.html（got {nav_url[:80]}），abort"}

    browser = api.registry.browser          # 与 Agent 工具共享的同一 BrowserController 实例
    # 等本次 fixture 的真实 DOM 就绪（btn_query 存在即为 dynamic.html 已渲染）
    got_btn = False
    for _ in range(30):
        try:
            if browser._evaluate('!!document.getElementById("btn_query")'):
                got_btn = True
                break
        except Exception:
            pass
        time.sleep(0.2)
    if not got_btn:
        return {"ready": False, "reason": "btn_query 不存在：未加载最新 dynamic.html（旧/缺 fixture），abort"}

    # ① anchor 可发现（语义锚能在非聊天页建立）
    st = browser.semantic_state()
    if not st or st.get("error") or not st.get("fingerprint"):
        return {"ready": False, "reason": f"语义锚不可建立: {st}"}
    print(f"[a2/ready] anchor ok -> fingerprint={st.get('fingerprint')} "
          f"sem_blocks={st.get('sem_block_count')} loading={st.get('loading')}")

    # ② driver 内部预置 pending baseline（冻结 P2-6 方法；非 Agent 工具）
    base = browser.capture_wait_baseline()
    ok_base = bool(base.get("pending")) and (base.get("sem_block_count") or 0) > 0
    print("[a2/ready] capture_wait_baseline ->", base)
    if not ok_base:
        return {"ready": False,
                "reason": f"baseline 未成功预置（pending={base.get('pending')} "
                          f"sem_block_count={base.get('sem_block_count')}），abort"}

    return {"ready": True, "anchor": st, "baseline": base}


def _task_text(url: str) -> str:
    return (
        f"本地动态 Benchmark 页面已就绪（已加载 dynamic.html，请勿再调用 browser_launch / browser_navigate）："
        f"{url}。这是一个『城市天气查询』页面：城市输入框、『查询』按钮、加载态、下方结果日志容器。\n"
        "任务：\n"
        "1) 先用 browser_find 定位城市输入框（placeholder 含『城市』），取得其 Target Handle，"
        "用 browser_type(target=<handle>, \"北京\") 输入；\n"
        "2) 再用 browser_find 定位『查询』按钮，取得其 Target Handle，用 browser_click(target=<handle>) 点击；\n"
        "3) 点击后**不要**自己 sleep，也不要 browser_wait 猜 selector、更不要整页读取来构造答案，"
        "请用 browser_wait_for_change() 等待结果区域动态更新；\n"
        "4) browser_wait_for_change() 返回的 message_delta 里就是更新后的结构化结果"
        "（城市/天气/温度），据此把结果简明返回。"
    )


def _summarize(agent, answer: str) -> int:
    print("\n===== Agent 终答 =====")
    print(answer or "(空)")

    run_log = getattr(agent, "_run_log", None)
    if not run_log or not os.path.exists(run_log):
        print("[a2] 未找到 run_log:", run_log)
        return 2

    # P2-8 A2 oracle（方案 1）：task_success 绑定 wait_delta.message_delta，
    # 而非 Agent 对结果的排版（表格/冒号不影响判定）。
    hit_delta, delta_ok, missing = _oracle_task_success(run_log)
    hit_final = _oracle_hit_answer(answer)  # 终答旧路径，仅作对比记录
    s = aggregate_run_log(run_log, task_success=hit_delta)

    print("\n" + "=" * 60)
    print(f"===== A2 Local Dynamic Benchmark Summary（{os.path.basename(run_log)}） =====")
    print(f"  task_success        = {s['task_success']}   (message_delta 三要素命中: {delta_ok})")
    print(f"  oracle_source       = wait_delta.message_delta（P2-8 方案 1，不再依赖终答排版）")
    print(f"  message_delta       = {extract_wait_delta(run_log)!r}")
    print(f"  终答旧路径命中      = {hit_final}（仅对比；def 若 True 判 False 即 oracle 假阴性样本）")
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


def main():
    self_check = "--self-check" in sys.argv
    port = int(os.environ.get("A2_PORT", "8712"))
    url = f"http://127.0.0.1:{port}/dynamic.html?bench={int(time.time() * 1000)}"

    httpd = _serve(DEMO_DIR, port)
    print(f"[a2] 本地动态服务起在 {url}")

    config = load_config()
    agent = Agent(config=config, confirm_handler=_confirm_handler)
    api = agent.api
    try:
        ready = _a2_ready(api, url)
        if not ready["ready"]:
            print(f"\n[a2] Fixture readiness FAILED —— abort（不执行 Agent task）：\n     {ready['reason']}")
            return 3
        print("[a2/ready] fixture ready（URL 正确 / anchor ok / baseline 已预置）\n")

        if self_check:
            print("[a2] self-check：跳过 Agent task，仅验证 driver 链路。")
            print("[a2/ready] anchor   =", ready.get("anchor"))
            print("[a2/ready] baseline =", ready.get("baseline"))
            return 0

        task = _task_text(url)
        print("[a2] 任务:", task, "\n")
        answer = agent.run(task)
        return _summarize(agent, answer)
    except Exception as e:
        print("[a2] EXCEPTION:", repr(e)[:300])
        return 2
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        _close_browser(api)


if __name__ == "__main__":
    sys.exit(main())