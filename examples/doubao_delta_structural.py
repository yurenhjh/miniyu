# -*- coding: utf-8 -*-
"""Phase D 结构侦察：区分「assistant 正文」vs「推荐 chips」的 DOM 结构。

策略：进入侧栏最近会话 → baseline → 发一次『你好』 → wait_for_changes COMPLETED →
对 message-list 锚内全部“可点文本叶”与“可见文本含文本节点”，按 DOM 结构打标：
  tag / role / 是否 button-like / 所在子树是否在 assistant 消息区 / rect / class前60字符 / text
以确认 chips 对应哪种 node（content vs control-like），为结构过滤（非文本黑名单）提供依据。
"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.browser_controller import BrowserController
from doubao_wait_primitive import _enter_recent, _cfg  # 复用进入会话逻辑

_JS = r"""
(() => {
  const root = document.querySelector('[data-miniyu-anchor]') || document.body;
  const vis = (el) => { const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    return true; };
  const notes = [];
  const seen = new Set();
  const walk = (el, depth) => {
    if (depth > 9) return;
    let txt = (el.innerText || '').trim().replace(/\s+/g, ' ');
    let own = txt;
    const kids = Array.from(el.children);
    for (const k of kids) if (k.innerText) own = own.replace(new RegExp((''+k.innerText).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g'), '').trim();
    own = own.replace(/\s+/g,' ').trim();
    const rich = own.length > 0;
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const role = el.getAttribute && el.getAttribute('role');
    const clickable = role === 'button' || role === 'link' || /button|radio|checkbox/.test(role || '') ||
                      (el.onclick != null) || (el.tagName === 'BUTTON' || el.tagName === 'A') ||
                      /cursor[\-:]pointer/.test(getComputedStyle(el).cursor);
    const rEl = el.getBoundingClientRect();
    let cls = ((el.className && el.className.baseVal !== undefined) ? el.className.baseVal : el.className || '');
    cls = (''+cls).split(/\s+/).slice(0, 8).join(' ');
    if (rich || clickable || tag === 'button') {
      const id = tag+'|'+role+'|'+clickable+'|'+cls.slice(0,24)+'|'+txt.slice(0,20);
      if (!seen.has(id)) {
        seen.add(id);
        notes.push({ tag, role: role || null, clickable, ownText: own.slice(0, 40),
                     text: txt.slice(0, 40), cls: cls.slice(0, 60),
                     rect: [Math.round(rEl.left), Math.round(rEl.top), Math.round(rEl.width), Math.round(rEl.height)],
                     children: kids.length, depth });
      }
    }
    for (const k of kids) walk(k, depth + 1);
  };
  walk(root, 0);
  return notes.slice(0, 120);
})()
"""

def main():
    cfg = _cfg()
    profile = os.path.expandvars(cfg.get("user_data_dir") or "")
    bc = BrowserController(ws_url="placeholder")
    bc.launch(port=9222, headless=cfg.get("headless", False),
              chrome_path=cfg.get("executable") or None, user_data_dir=profile or None)
    print("[1] 浏览器已启动")
    bc.navigate("https://www.doubao.com/chat")
    for _ in range(40):
        try:
            if (bc._evaluate("document.body ? document.body.innerText.length : 0") or 0) > 40: break
        except Exception: pass
        time.sleep(0.5)
    print("[2] 页面已加载")

    print("[3] 进入侧栏最近会话…")
    entered = _enter_recent(bc) or {}
    print("    点击结果：", json.dumps(entered, ensure_ascii=False))
    time.sleep(2.0)

    for _ in range(10):
        if bc.discover_message_root(baseline_user_text="你好").get("ok"): break
        time.sleep(0.6)
    if not bc._evaluate("!!document.querySelector('[data-miniyu-anchor]')"):
        print("[ABORT] 未进入真实会话（无 message-root 锚）；exit=1")
        bc.close(); sys.exit(1)

    # 等历史渲染完成 + baseline
    for _ in range(12):
        st = bc.semantic_state() or {}
        if (st.get("sem_block_count") or 0) > 0: break
        time.sleep(0.5)
    baseline = bc.semantic_state() or {}
    print("[4] baseline sem_blocks=%s fp=%s" % (baseline.get("sem_block_count"), baseline.get("fingerprint")))

    print("[5] 发送『你好』…")
    items = bc.find(role="textbox")
    bc.type_text("你好", target=items[0]["handle"])
    bc.press_enter()
    print("    已发送")

    print("[6] 等待回复完成…")
    res = bc.wait_for_changes(baseline=baseline, timeout=60.0, min_stable_rounds=2, interval=1.0)
    print("    state=%s success=%s elapsed_ms=%s" % (res.get("state"), res.get("success"), res.get("elapsed_ms")))
    delta = res.get("message_delta") or ""
    print("    assistant_delta=", delta.replace("\n", " ⏎ "))

    print("\n[D] message-root 内 seesaw 节点（append 自己文本的叶子）:")
    notes = bc._evaluate(_JS) or []
    for n in notes:
        print("  %-4s role=%-8s click=%-5s own=%-14s txt=%-14s kids=%d d=%d cls=%s"
              % (n["tag"], n["role"], n["clickable"], n["ownText"], n["text"], n["children"], n["depth"], n["cls"]))
    out = Path(__file__).resolve().parent.parent / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    fp = out / ("doubao_delta_structural_%d.json" % int(time.time()*1000))
    fp.write_text(json.dumps({"state": res.get("state"), "delta": delta,
                              "notes": notes}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[DONE] 报告：", fp)
    bc.close()

if __name__ == "__main__":
    main()