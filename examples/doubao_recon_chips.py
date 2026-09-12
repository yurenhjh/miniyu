# -*- coding: utf-8 -*-
"""Phase D 侦察(不发消息)：进入真实会话后，定位「推荐 chips」与「assistant 正文」两类节点，
对比其 DOM 结构（tag/role/class 全量/rect/父链 classs 集合），为结构过滤提供依据。"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.browser_controller import BrowserController
from doubao_wait_primitive import _enter_recent, _cfg

_JS = r"""
(() => {
  const vis = (el) => { const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    return true; };
  const cls = (el) => { let c = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : el.className;
    return ('' + (c||'')).split(/\s+/).filter(Boolean); };
  const parentCls = (el, n) => { const arr = []; let p = el.parentElement;
    for (let i=0; p && i<n; i++, p=p.parentElement) arr.push((p.tagName||'').toLowerCase() + ':' + cls(p).slice(0,6).join('.'));
    return arr; };
  const info = (el) => { const r = el.getBoundingClientRect(); const c = getComputedStyle(el);
    return { tag: (el.tagName||'').toLowerCase(), role: el.getAttribute ? (el.getAttribute('role')||'') : '',
             cls: cls(el).slice(0,12), clsAll: cls(el),
             rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
             cursor: c.cursor, parent: parentCls(el, 5) }; };
  const keys = ['你叫什么名字','你可以做些什么','你能做些什么','你是如何学习','你是如何学习的','你可以做什么','你叫什么'];
  const out = { chips: [], content: [] };
  // 全文档按文本片段查 chips
  for (const el of Array.from(document.querySelectorAll('button, [role="button"], [role="link"], div, span, a'))) {
    const t = ((el.innerText||'')+'').trim().replace(/\s+/g,' ');
    if (!t || t.length > 24 || !vis(el)) continue;
    if (keys.some(k => t.indexOf(k) >= 0)) out.chips.push({ text: t, ...info(el) });
  }
  // assistant 正文：含『站点模板』或『有什么需要我帮忙』的正文块
  const ck = ['有什么需要我帮忙','继续配置那个站点模板','直接讲就行'];
  for (const el of Array.from(document.querySelectorAll('main *'))) {
    const t = ((el.innerText||'')+'').trim().replace(/\s+/g,' ');
    if (!t || t.length > 120 || t.length < 8 || !vis(el)) continue;
    if (ck.some(k => t.indexOf(k) >= 0)) out.content.push({ text: t.slice(0,50), ...info(el) });
  }
  return out;
})()
"""

def main():
    cfg = _cfg(); profile = os.path.expandvars(cfg.get("user_data_dir") or "")
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
    print("    ", json.dumps(_enter_recent(bc) or {}, ensure_ascii=False))
    time.sleep(2.0)
    for _ in range(10):
        if bc.discover_message_root(baseline_user_text="你好").get("ok"): break
        time.sleep(0.6)
    if not bc._evaluate("!!document.querySelector('[data-miniyu-anchor]')"): print("[ABORT] 无锚"); bc.close(); sys.exit(1)
    for _ in range(12):
        if (bc.semantic_state() or {}).get("sem_block_count", 0) > 0: break
        time.sleep(0.5)

    res = bc._evaluate(_JS) or {}
    print("\n================ 推荐 CHIPS ================")
    for c in res.get("chips") or []:
        print("  [%s role=%s cur=%s] rect=%s" % (c["tag"], c["role"] or '-', c["cursor"], c["rect"]))
        print("     cls=%s" % " ".join(c["cls"]))
        print("     parent=%s" % " <- ".join(c["parent"]))
        print("     text=%s" % c["text"])
    print("================ assistant 正文候选 ================")
    for c in res.get("content") or []:
        print("  [%s role=%s cur=%s] rect=%s" % (c["tag"], c["role"] or '-', c["cursor"], c["rect"]))
        print("     cls=%s" % " ".join(c["cls"]))
        print("     text=%s" % c["text"])
    out = Path(__file__).resolve().parent.parent / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    fp = out / ("doubao_recon_chips_%d.json" % int(time.time()*1000))
    fp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[DONE] 报告：", fp)
    bc.close()

if __name__ == "__main__":
    main()