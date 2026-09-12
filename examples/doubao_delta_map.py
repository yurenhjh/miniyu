# -*- coding: utf-8 -*-
"""Phase D 侦察(需发送,COMPLETED 后立即执行)：把 delta 每一行映射回 message-root 内
对应 DOM 叶节点并打结构标(tag/role/clickable/class/cursor/rect/父链)，确认 chips
与 assistant 正文的结构差异。"""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.browser_controller import BrowserController
from doubao_wait_primitive import _enter_recent, _cfg

_MAP_JS = r"""
(() => {
  const lines = __LINES__;
  const root = document.querySelector('[data-miniyu-anchor]') || document.body;
  const vis = (el) => { const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const c = getComputedStyle(el);
    return c.display!=='none' && c.visibility!=='hidden' && c.opacity!=='0'; };
  const cls = (el) => { let c=(el.className&&el.className.baseVal!==undefined)?el.className.baseVal:el.className; return (''+(c||'')).split(/\s+/).filter(Boolean); };
  const par = (el) => { const a=[]; for(let p=el.parentElement; p && a.length<6; p=p.parentElement){
      const cc=cls(p); a.push((p.tagName||'').toLowerCase()+':'+cc.filter(x=>!/^(opacity-|h-|w-|p-|pr-|pl-|mx-|my-|max-w-)/.test(x)).slice(0,4).join('.')); } return a; };
  // 全量叶子文本元素（innerText==textContent，即直接持有文本的节点）
  const leaves = [];
  for (const el of Array.from(root.querySelectorAll('*'))) {
    const it = (el.innerText||''); const tc = (el.textContent||'');
    if (!it || !vis(el)) continue;
    if (it.trim() !== tc.trim()) continue;                       // 叶子：无子文本节点
    const t = it.trim().replace(/\s+/g,' ');
    if (!t) continue;
    const r = el.getBoundingClientRect();
    const role = el.getAttribute ? (el.getAttribute('role')||'') : '';
    const c = getComputedStyle(el);
    const clickable = role==='button'||role==='link'||/^(button|checkbox|radio)$/.test(role)||
                      (el.tagName==='BUTTON'||el.tagName==='A')||/pointer/.test(c.cursor)||
                      /^(button|link|click|tap|chip|suggest|recommend|action)/i.test(el.className||'');
    leaves.push({ el, t, tag:(el.tagName||'').toLowerCase(), role, clickable,
                  cls:cls(el).slice(0,10), rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
                  parent:par(el) });
  }
  const byNormal = {};
  for (const L of leaves) { const k=L.t.replace(/\s+/g,' '); (byNormal[k]=byNormal[k]||[]).push(L); }
  const exact = [];
  for (const ln of lines) { const k=ln.replace(/\s+/g,' '); if (byNormal[k]) exact.push({line:ln, nodes:byNormal[k].slice(0,3).map(m=>({tag:m.tag,role:m.role,clickable:m.clickable,cls:m.cls,rect:m.rect,parent:m.parent}))}); }
  // 结构汇总：所有短文本(<=24)且 control-like 的 leaf
  const controls = leaves.filter(L=>L.t.length<=24 && L.clickable);
  return { exact, controlLeaves: controls.slice(0,40).map(m=>({text:m.t,tag:m.tag,role:m.role,cls:m.cls,rect:m.rect,parent:m.parent})) };
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
    print("[3] 进入侧栏最近会话…"); print("    ", json.dumps(_enter_recent(bc) or {}, ensure_ascii=False)); time.sleep(2.0)
    for _ in range(10):
        if bc.discover_message_root(baseline_user_text="你好").get("ok"): break
        time.sleep(0.6)
    for _ in range(12):
        if (bc.semantic_state() or {}).get("sem_block_count", 0) > 0: break
        time.sleep(0.5)
    baseline = bc.semantic_state() or {}
    print("[4] baseline fp=%s blocks=%s" % (baseline.get("fingerprint"), baseline.get("sem_block_count")))
    items = bc.find(role="textbox"); bc.type_text("你好", target=items[0]["handle"]); bc.press_enter()
    print("[5] 已发送『你好』")
    res = bc.wait_for_changes(baseline=baseline, timeout=60.0, min_stable_rounds=2, interval=1.0)
    delta = res.get("message_delta") or ""
    print("[6] state=%s"%res.get("state")); print("    delta 行:"); [print("      |%s|"%l) for l in delta.split("\n")]
    m = bc._evaluate(_MAP_JS.replace("__LINES__", json.dumps(delta.split("\n")))) or {}
    print("\n==== delta 行 ↔ DOM 节点 精确映射 ====")
    for e in m.get("exact") or []:
        print("  行『%s』" % e["line"])
        for nd in e["nodes"]:
            print("     [%s role=%s click=%s] rect=%s" % (nd["tag"], nd["role"] or '-', nd["clickable"], nd["rect"]))
            print("       cls=%s" % " ".join(nd["cls"]))
            print("       parent=%s" % " <- ".join(nd["parent"]))
    print("\n==== control-like 短叶节点 (<=24字, 可能=chips) ====")
    for c in m.get("controlLeaves") or []:
        print("  『%s』[%s role=%s] rect=%s cls=%s" % (c["text"], c["tag"], c.get("role") or '-', c["rect"], " ".join(c["cls"][:6])))
    out = Path(__file__).resolve().parent.parent / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    fp = out / ("doubao_delta_map_%d.json" % int(time.time()*1000))
    fp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[REPORT] ", fp); bc.close()

if __name__ == "__main__":
    main()