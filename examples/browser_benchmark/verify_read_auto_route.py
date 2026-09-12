#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_read_auto_route.py — P2-8 第二批 read-contract v2（B）：0-token 真实浏览器验证

目标：证明 related-content handle 在【不传 mode】的普通 read_text(target=<related_handle>)
下也能自动路由到正文区块读取（B），同时确认普通 handle 读到的是标题本体（不受影响）。

链路：
  find(Examples 标题锚) → related_content.handle
  → read_text(target=<related_handle>)            # 无 mode → 自动 content read
  → 必须得到 "Creating a basic button" 等正文
  → read_text(target=<元素自身handle>)             # 无 mode → 仍是单节点读 "Examples"
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.browser_controller import BrowserController

URL = "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/button"


def main():
    bc = BrowserController()
    try:
        bc.launch(port=9234, headless=True)
        bc.navigate(URL)
        for _ in range(60):
            try:
                if bc._evaluate('document.readyState === "complete"') and bc._evaluate('document.querySelector("h2")'):
                    break
            except Exception:
                pass
            time.sleep(0.3)
        print(f"[v] loaded, title={bc._evaluate('document.title') or ''!r}")

        hits = bc.find(text="Examples", role="link") or []
        print(f"[v] find(role=link, text=Examples) → {len(hits)} 候选")
        target = None
        for it in hits:
            has_rc = bool(it.get("related_content"))
            print("     ", {k: it.get(k) for k in ("handle", "ref", "tag")}, "related_content=", has_rc)
            # 真正的 Examples 小节标题锚：带 related_content 且路径落在 main/h2
            if not target and has_rc and it.get("ref") and "main" in it["ref"]:
                target = it
        if not target:
            print("[v] FAIL：未找到 Examples 标题锚（含 main 路径）")
            return 2
        rc = target.get("related_content")
        if not rc or not rc.get("handle"):
            print(f"[v] FAIL：标题锚 {target.get('handle')} 无 related_content.handle")
            return 3
        rel_h = rc["handle"]
        print(f"[v] 标题锚 handle={target.get('handle')} related_content.handle={rel_h}")
        print(f"[v] related_content preview={rc.get('preview')!r} char_count={rc.get('char_count')} truncated={rc.get('truncated')}")

        # B：不带 mode 读 related handle → 应自动路由到正文
        body = bc.read_text(target=rel_h)
        print(f"[v] read_text(target={rel_h}) [无 mode] → {body!r}")
        ok_body = isinstance(body, str) and "Creating a basic button" in body
        print(f"[v] 正文自动路由 {'OK' if ok_body else 'FAIL'}")

        # 普通 handle（元素本体）不带 mode → 仍是单节点读标题
        own = bc.read_text(target=target.get("handle"))
        print(f"[v] read_text(target={target.get('handle')}) [无 mode] → {own!r}")
        ok_own = isinstance(own, str) and own.strip() == "Examples"
        print(f"[v] 普通 handle 单节点读 {'OK' if ok_own else 'FAIL'}")

        ok = ok_body and ok_own
        print(f"\n[v] 结论: read auto-route（B）0-token 验证 = {'PASS' if ok else 'FAIL'}  (URL={URL})")
        return 0 if ok else 4
    except Exception as e:
        print("[v] EXCEPTION:", repr(e)[:240])
        return 1
    finally:
        try:
            bc.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())