"""
browser_deepseek_demo.py
第4组 · examples —— CDP 真机示例：给 DeepSeek 发一句"你好"并读回复（需登录）

与 browser_doubao_demo.py 共用同一套"通用浏览器对话"交互（真实鼠标点击聚焦 →
清空 → insertText 中文 → 回车 → 读页面文本），**唯一差别是目标站点 DeepSeek 必须登录**
（无游客模式，且会话 cookie 不跨进程，浏览器重启后需重登）。想跑"免登录、可无人值守"
版本请用 browser_doubao_demo.py。

运行前置（重要）：
    1. 用"独立档案 + 调试端口"启动 Edge（新版 Chrome/Edge ≥136 禁止远程调试作用于默认档案）：

         & "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" ^
             --remote-debugging-port=9223 ^
             --remote-allow-origins=* ^
             --user-data-dir="%TEMP%\\edge_ds_profile" ^
             "https://chat.deepseek.com/"

    2. 若该档案未登录过 DeepSeek，在弹出的窗口里用 微信扫码 / 手机号 / Apple 登录一次。
    3. 运行本脚本（默认端口 9223）：

         python examples/browser_deepseek_demo.py                     # 发"你好"
         python examples/browser_deepseek_demo.py --port 9223 --message "介绍一下你自己"

本脚本验证步骤：先 DOM 确认在对话页（有可编辑输入条）→ 鼠标点击聚焦 → 清空 → 输入 →
回车前截图确认文字进框 → 回车发送 → 读回复 + 最终截图。若停在登录墙则报错退出。

依赖：core.browser_controller（websocket-client）。
"""

import argparse
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.browser_controller import BrowserController  # noqa: E402


# ---- 与 browser_doubao_demo.py 一致的小工具：坐标点击 / 组合键（不改动 core） ----
def mouse_click(b, x, y):
    """在视口坐标 (x, y) 发一次真实左键点击（CDP 浏览器级，可信）"""
    for typ in ("mousePressed", "mouseReleased"):
        b._send("Input.dispatchMouseEvent", {
            "type": typ, "x": int(x), "y": int(y),
            "button": "left", "clickCount": 1})


def key_event(b, event_type, key, code, vk, modifiers=0):
    b._send("Input.dispatchKeyEvent", {
        "type": event_type, "key": key, "code": code,
        "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
        "modifiers": modifiers})


def ctrl_a_delete(b):
    for typ in ("keyDown", "keyUp"):
        key_event(b, typ, "a", "KeyA", 65, modifiers=2)
    for typ in ("keyDown", "keyUp"):
        key_event(b, typ, "Backspace", "Backspace", 8)


def press_enter(b):
    key_event(b, "rawKeyDown", "Enter", "Enter", 13)
    key_event(b, "keyUp", "Enter", "Enter", 13)


# 定位"可见的聊天输入条"（contenteditable / role=textbox / textarea 里最宽的那个）
_JS_PICK = r"""
(() => {
  const isEd = e => e && (e.isContentEditable || e.tagName === 'TEXTAREA' ||
                          (e.getAttribute && e.getAttribute('role') === 'textbox'));
  const all = Array.from(document.querySelectorAll(
      '[contenteditable="true"],textarea,[role="textbox"]'))
    .filter(el => { const r = el.getBoundingClientRect();
      return r.width > 150 && r.height > 20 && r.height < 300 && r.bottom > innerHeight * 0.4; });
  if (!all.length) return null;
  all.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
  const r = all[0].getBoundingClientRect();
  return { x: Math.round(r.left + r.width / 2),
           y: Math.round(r.top + Math.min(40, r.height / 2)) };
})()
"""


def read_active(b):
    return b._evaluate(
        "(() => { const e = document.activeElement;"
        " return e ? (e.value !== undefined ? e.value : e.innerText) : ''; })()") or ""


def main():
    ap = argparse.ArgumentParser(description="CDP 驱动 DeepSeek 对话发一条消息并读回复（需登录）")
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--url", default="https://chat.deepseek.com/")
    ap.add_argument("--message", default="你好")
    ap.add_argument("--out", default=None, help="最终截图文件名（默认存本目录）")
    ap.add_argument("--wait-reply", type=float, default=8.0)
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(__file__))
    final_shot = args.out or os.path.join(out_dir, "deepseek_demo_result.png")

    b = BrowserController()
    _ = b.attach(port=args.port)
    time.sleep(1.5)
    print("url   =", b._evaluate("location.href"))
    if "deepseek.com" not in (b._evaluate("location.href") or ""):
        b.navigate(args.url)
        time.sleep(4.0)
    print("url2  =", b._evaluate("location.href"))

    has_ed = b._evaluate(
        "!!document.querySelector('textarea,[contenteditable=\"true\"],[role=\"textbox\"]')")
    body0 = (b._evaluate("document.body ? document.body.innerText : ''") or "")[:150]
    print("has_editable =", has_ed)
    if not has_ed:
        print("!! 未检测到可编辑输入条，疑似登录墙/引导页。请在可见窗口登录后重跑。")
        print("body 片段:", repr(body0))
        return 3

    pos = b._evaluate(_JS_PICK)
    if not pos:
        print("!! 定位输入条失败，中止。")
        return 3
    print("composer_at =", pos)
    mouse_click(b, pos["x"], pos["y"])
    time.sleep(0.6)

    ctrl_a_delete(b)
    time.sleep(0.3)
    b._send("Input.insertText", {"text": args.message})
    time.sleep(0.4)
    typed_shot = os.path.join(tempfile.gettempdir(), "deepseek_demo_typed.png")
    print("typed-shot（发出前，应能看到消息在输入条里）=", b.screenshot(typed_shot))

    press_enter(b)
    print("已发送:", args.message)
    time.sleep(args.wait_reply)
    tail = (b._evaluate("document.body ? document.body.innerText : ''") or "")[-1200:]
    print("---- 页面尾部文本（AI 回复区） ----")
    print(tail)

    shot = b.screenshot(final_shot)
    print("final-shot =", shot)

    still = args.message in read_active(b)
    if still:
        print("FAIL —— 消息未发出去（输入条里仍有内容）")
        return 1
    print("OK —— 已向 DeepSeek 发送并读到回复（需登录账号）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
