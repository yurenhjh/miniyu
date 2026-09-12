# -*- coding: utf-8 -*-
"""一次性诊断：发送『你好』后等 6 秒，定位 assistant 回复文本的真实 DOM 容器。
不猜 selector，dump 稳定态下 main 内所有含 message/conversation 语义的容器 + 回复文本位置。
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


_DUMP_JS = r"""
(() => {
  const main = document.querySelector('main');
  const out = { mainText: main ? (main.innerText || '').slice(-600) : null };
  out.containers = [];
  if (main) {
    const sels = ['[class*="message" i]', '[class*="conversation" i]', '[class*="chat" i]', '[class*="bubble" i]'];
    const seen = new Set();
    for (const s of sels) for (const el of main.querySelectorAll(s)) {
      if (seen.has(el) || el.offsetParent === null) continue; seen.add(el);
      const r = el.getBoundingClientRect();
      if (r.height < 2) continue;
      out.containers.push({ sel: s, tag: el.tagName.toLowerCase(),
        cls: ((el.className||'')+'').replace(/\s+/g,' ').slice(0,60),
        rect: [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
        text: (el.innerText||'').trim().slice(-140) });
      if (out.containers.length >= 40) break;
    }
  }
  return out;
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
            if (bc._evaluate("document.body ? document.body.innerText.length : 0") or 0) > 50:
                break
        except Exception:
            pass
        time.sleep(0.5)
    print("[2] 页面已加载")
    ok = False
    for _ in range(3):
        try:
            items = bc.find(role="textbox")
            if not items: time.sleep(0.6); continue
            bc.type_text("你好", target=items[0]["handle"])
            bc.press_enter(); ok = True; break
        except Exception:
            time.sleep(0.6)
    print("[3] 发送成功" if ok else "[FAIL] 发送失败")
    print("[4] 等待 7 秒让回复稳定…")
    # 每 1.5s dump 一次，观察回复容器出现过程
    for i in range(5):
        time.sleep(1.4 if i == 0 else 1.2)
        d = bc._evaluate(_DUMP_JS) or {}
        texts = [c["text"] for c in d.get("containers", []) if c["text"]]
        hit = [c for c in d.get("containers", []) if "你好呀" in c["text"] or "帮忙的是" in c["text"]
               or "帮你的吗" in c["text"]]
        print(("  [t+%.1fs] mainText=%s | 容器%d个 | 回复命中容器%d个"
               % (i * 1.3 + 1.4, (d.get("mainText") or "").replace(chr(10), "⏎")[-80:],
                  len(d.get("containers", [])), len(hit))))
        for c in hit[:5]:
            print("    HIT cls=%s rect=%s text=%s" % (c["cls"], c["rect"], c["text"][-80:]))
    out_file = Path(__file__).resolve().parent.parent / "outputs" / ("reply_locate_%d.json" % int(time.time() * 1000))
    out_file.write_text(json.dumps(bc._evaluate(_DUMP_JS) or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[DONE] 报告：", out_file)
    try:
        bc.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()