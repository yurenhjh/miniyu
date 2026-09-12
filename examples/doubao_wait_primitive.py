# -*- coding: utf-8 -*-
"""P2-5 primitive v3（gtp 评审执行稿，Plan 1）：
「进入侧栏最近会话 → 确认真实聊天主体 → 记录 baseline → 单次发送『你好』 → 状态机等待 →
 计算 semantic delta → 拿 assistant 真实回复」。

硬约束：
  1. 只发送一次（失败/超时都不自动二次发送，extra_send=0）
  2. baseline 完整记录 anchor / fingerprint / message_count / loading / user_message_text
  3. 状态显式输出：WAITING/STARTED/GENERATING/COMPLETED；失败 ANCHOR_LOST/NO_CHANGE/TIMEOUT/LOADING_STUCK
  4. 全程浏览器控制层确定性执行：0 LLM 猜 selector / 0 SoM / 0 截图 / 0 坐标点击
  5. 不用孙斌“最大 div”回退：message root 找不到 → 直接 ANCHOR_LOST 并暴露真实原因
  6. 记录 baseline fingerprint → final fingerprint → changed=true → assistant delta=...
"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.browser_controller import BrowserController


def _cfg():
    try:
        from core.agent_config import load_config
        return load_config().get("browser", {}) or {}
    except Exception:
        return {}


# 进入侧栏最近会话（DOM .click()，非坐标/非 SoM）
# 侦察结论：/chat 首页会话列表默认折叠，仅侧栏存在 a[class*="conversation-item"] 时可见；
#   否则需先点主区域居中「对话/工作」模式开关(bottom x≈500,y≈355, innerText=对话)露出列表。
#   ——详见 examples/_dbg_click_tab.py。若已展开则直接点最上一条最近会话。
_ENTER_RECENT_JS = r"""
(() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    return true;
  };
  // 1) 严格匹配会话项（杜绝误点 nav_item：nav_item 含 "item" 但类名不含 "conversation-item"）
  const anchors = [];
  for (const el of document.querySelectorAll('a[class*="conversation-item" i]:not([class*="sidebar_nav_item" i]), [class*="conversation-item" i][role="button"]:not([class*="sidebar_nav_item" i])')) {
    const r = el.getBoundingClientRect();
    if (vis(el)) anchors.push({ el, x: r.left, y: r.top });
  }
  if (anchors.length) {
    anchors.sort((a, b) => a.y - b.y || a.x - b.x);
    const h = anchors[0];
    h.el.click();
    return { clicked: true, method: 'conv-item', x: Math.round(h.x), y: Math.round(h.y),
             text: (h.el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 24) };
  }
  // 2) 会话列表折叠：点「对话」模式开关露出（window 标志保证只点一次）
  if (!window._miniyuRevealTried) {
    for (const el of document.querySelectorAll('button, [role="button"], [role="tab"]')) {
      if ((el.innerText || '').trim() !== '对话') continue;
      if (!vis(el)) continue;
      window._miniyuRevealTried = true;
      const r = el.getBoundingClientRect();
      el.click();
      return { clicked: false, clickedTab: true, x: Math.round(r.left), y: Math.round(r.y) };
    }
  }
  // 3) reveal 已试：持续重查会话项（等待列表懒加载/动画完成）
  const anchors2 = [];
  for (const el of document.querySelectorAll('a[class*="conversation-item" i]:not([class*="sidebar_nav_item" i]), [class*="conversation-item" i][role="button"]:not([class*="sidebar_nav_item" i])')) {
    const r = el.getBoundingClientRect();
    if (vis(el)) anchors2.push({ el, x: r.left, y: r.top });
  }
  if (anchors2.length) {
    anchors2.sort((a, b) => a.y - b.y || a.x - b.x);
    const h = anchors2[0];
    h.el.click();
    return { clicked: true, method: 'conv-item-after-reveal', x: Math.round(h.x), y: Math.round(h.y),
             text: (h.el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 24) };
  }
  window._miniyuRevealWait = (window._miniyuRevealWait || 0) + 1;
  if (window._miniyuRevealWait >= 14) {
    const convs = Array.from(document.querySelectorAll('[class*="conversation-item" i]:not([class*="sidebar_nav_item" i])'));
    return { clicked: false, diag: {
      convCount: convs.length,
      convSample: convs.slice(0, 6).map(el => { const r = el.getBoundingClientRect(); const c = getComputedStyle(el);
        return { tag: el.tagName.toLowerCase(), vis: vis(el), op: c.opacity, d: c.display,
                 x: Math.round(r.left), y: Math.round(r.top), txt: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 18) }; }),
      navCount: document.querySelectorAll('a[class*="sidebar_nav_item" i]').length,
      mainText: ((document.querySelector('main') || {}).innerText || '').slice(0, 140).replace(/\n/g, '⏎'),
    } };
  }
  return { clicked: false, waiting: true };
})()
"""


def _enter_recent(bc):
    state = {}
    for _ in range(20):
        res = bc._evaluate(_ENTER_RECENT_JS) or {}
        state = res
        if res.get("clicked"):
            return res
        if res.get("diag"):
            break
        time.sleep(0.5)
    return state


def main():
    cfg = _cfg()
    profile = os.path.expandvars(cfg.get("user_data_dir") or "")
    bc = BrowserController(ws_url="placeholder")
    report = {"llm": 0, "selector_guess": 0, "som": 0, "screenshot": 0, "coordinate_click": 0,
              "extra_send": 0, "send_count": 0}

    bc.launch(port=9222, headless=cfg.get("headless", False),
              chrome_path=cfg.get("executable") or None, user_data_dir=profile or None)
    print("[1] 浏览器已启动")
    bc.navigate("https://www.doubao.com/chat")
    for _ in range(40):
        try:
            if (bc._evaluate("document.body ? document.body.innerText.length : 0") or 0) > 40:
                break
        except Exception:
            pass
        time.sleep(0.5)
    print("[2] 页面已加载")

    # ---- [Phase B] 落地态锚判定：未进会话的首页不应返回可信 anchor（禁贪大 div，应 ANCHOR_LOST）----
    hub = bc.discover_message_root(baseline_user_text="你好")
    hub_ok = bool(hub.get("ok"))
    print("    [Phase B] 落地态 anchor ok=%s（预期 False=ANCHOR_LOST） error=%r"
          % (hub_ok, (hub.get("error") or "")[:50]))
    if hub_ok:
        print("      （页面直接落进会话态，豁免本次落地态断言；不影响后续进入流程）")

    # ---- 进入侧栏最近会话 ----
    print("[3] 进入侧栏最近会话…")
    entered = _enter_recent(bc) or {}
    print("    点击结果：", json.dumps(entered, ensure_ascii=False))
    if not entered.get("clicked"):
        print("    未直接点中会话项；最近一次尝试：", json.dumps(entered, ensure_ascii=False))
    time.sleep(2.0)

    # ---- 确认真实聊天主体：message-list 结构锚（无 message-list => ANCHOR_LOST，不用贪大 div）----
    print("[4] 确认真实聊天主体 / 结构锚…")
    anchor = None
    cause = None
    for _ in range(10):
        anchor = bc.discover_message_root(baseline_user_text="你好")
        if anchor.get("ok"):
            break
        cause = anchor.get("error")
        time.sleep(0.6)
    best = (anchor or {}).get("best") or {}
    if not (anchor or {}).get("ok"):
        report["terminal"] = {"state": "ANCHOR_LOST",
                              "cause": cause or ("best_score=%s cls=%s" % (best.get("score"), best.get("cls")))}
        print("    [ANCHOR_LOST] 未拿到可信 conversation root：", report["terminal"]["cause"])
        print("    候选：")
        for c in ((anchor or {}).get("candidates") or []):
            print("      score=%3d reliable=%-5s cls=%s" % (c["score"], c.get("reliable"), c["cls"][:50]))
        _flush(bc, report); return
    print("    锚：", json.dumps((anchor.get("anchor") or {}).get("stem", best.get("stem")),
                                ensure_ascii=False),
          " score=%s" % best.get("score"))
    print("    anchor 详情：", json.dumps({k: best.get(k) for k in ("score", "reliable", "cls", "rect")},
                                         ensure_ascii=False))

    # ---- 等待会话内容完全渲染：message-root 存在但消息可能懒加载/动画中，
    #      语义蓝本必须捕获到既有历史，否则 delta=全历史（本轮关键修复点）。
    #      以 fingerprint 连续 2 轮稳定作为“历史渲染完成”信号，避免过早记录空/残缺基线。 ----
    prev_fp, stable_cnt = None, 0
    for _ in range(20):
        st0 = bc.semantic_state() or {}
        fp = st0.get("fingerprint")
        if st0.get("error") in (None, "anchor_lost") and fp and (st0.get("sem_block_count") or 0) > 0:
            stable_cnt = stable_cnt + 1 if fp == prev_fp else 0
            prev_fp = fp
            if stable_cnt >= 2:
                break
        time.sleep(0.5)

    # ---- baseline 完整记录（叶级分节，与 semantic_blocks 的 diff 一致）----
    baseline = bc.semantic_baseline(user_message_text="你好")
    baseline["anchor"] = {"stem": getattr(bc, "_message_root_stem", None), "cls": (best.get("cls") or "")}
    baseline["message_count"] = baseline.get("sem_block_count")
    report["baseline"] = {k: v for k, v in baseline.items() if k != "semantic_text"}
    report["baseline"]["semantic_text"] = (baseline.get("semantic_text") or "")[-160:]
    print("\n[5] baseline：", json.dumps({k: v for k, v in baseline.items() if k != "semantic_text"}, ensure_ascii=False))
    print("    baseline.semantic_text=", (baseline.get("semantic_text") or "")[-160:].replace(chr(10), "⏎"))
    print("    [check] baseline.sem_block_count=%s 已捕获历史叶行=%s："
          % (baseline.get("sem_block_count"), len(baseline.get("leaf_lines") or [])),
          "OK" if len(baseline.get("leaf_lines") or []) > 3 else "NO（会话未渲染，delta 将偏大）")

    # ---- 单次发送『你好』（不自动重发）----
    print("\n[6] 单次发送『你好』…")
    send_err = None
    try:
        items = bc.find(role="textbox")
        if not items:
            send_err = "未找到 textbox"
        else:
            bc.type_text("你好", target=items[0]["handle"])
            report["send_count"] += 1
            bc.press_enter()
    except Exception as e:
        send_err = str(e)[:80]
    if send_err:
        report["terminal"] = {"state": "SEND_FAILED", "cause": send_err}
        print("    [SEND_FAILED] %s（不二次发送）" % send_err)
        _flush(bc, report); return
    print("    已发送（send_count=1，extra_send=0）")

    # ---- 状态机等待（含 trace 显式各阶段）----
    print("\n[7] 状态机 wait_for_changes…")
    stamps = []
    result = bc.wait_for_changes(baseline=baseline, timeout=60.0, min_stable_rounds=2, interval=1.0,
                                 trace=lambda t: stamps.append("      [%d] state=%-11s loading=%-5s delta=%-5s stable=%d"
                                                               % (t["tick"], t["state"], t["loading"], t["delta"], t["stable"])))
    for s in stamps:
        print(s)
    final_fp = result.get("fingerprint")
    report["wait_result"] = {k: v for k, v in result.items() if k != "message_delta"}
    report["final_fingerprint"] = final_fp
    report["changed"] = (final_fp is not None) and (final_fp != baseline.get("fingerprint"))
    report["assistant_delta"] = result.get("message_delta") or ""
    report["message_blocks"] = result.get("message_blocks") or []

    print("\n    结果：", json.dumps({k: v for k, v in result.items() if k != "message_delta"}, ensure_ascii=False))
    print("    baseline fingerprint → final fingerprint  = %s → %s , changed=%s"
          % (baseline.get("fingerprint"), final_fp, report["changed"]))
    print("    assistant delta =", (report["assistant_delta"] or "(空)").replace("\n", "⏎"))
    print("    [blocks] 结构化块（Phase D）：")
    for b in report["message_blocks"]:
        print("      %-4s %-7s %s" % (b["handle"], b["kind"], b["text"].replace("\n", "⏎")))

    # 校验：delta 不应是推荐 chips / 时间 / 操作栏
    base_sem = set(baseline.get("semantic_text", "").split("\n"))
    chips = [l for l in report["assistant_delta"].split("\n") if l and l in base_sem]
    report["delta_contains_baseline_noise"] = chips
    print("\n[8] 校验推荐 chips/时间/操作栏未被当回复主体：", "OK（delta 与 baseline 无交叠）" if not chips else ("含基线行:" + str(chips)))

    _flush(bc, report)
    try:
        bc.close()
    except Exception:
        pass


def _flush(bc, report):
    out_dir = Path(__file__).resolve().parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / ("doubao_primitive_v3_%d.json" % int(time.time() * 1000))
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[DONE] primitive 报告：", out_file)
    print("\n===== 摘要 =====")
    for k in ("llm", "selector_guess", "som", "screenshot", "coordinate_click", "extra_send", "send_count"):
        print("  %s = %d" % (k, report[k]))
    if report.get("terminal"):
        print("  terminal =", json.dumps(report["terminal"], ensure_ascii=False))
    if report.get("wait_result"):
        print("  state =", report["wait_result"].get("state"))
        print("  changed =", report.get("changed"))
        print("  assistant_delta =", (report.get("assistant_delta") or "(空)").replace("\n", "⏎")[:200])


if __name__ == "__main__":
    main()