"""
browser_doubao_demo.py
第4组 · examples —— 免登录 AI 对话真机示例：给豆包（游客模式）发一句"你好"并读回复

与 browser_deepseek_demo.py（需登录）相对的"免登录"版本，实测区别（2026-09-05 真机验证）：

    1. 目标 = https://www.doubao.com/chat，**游客模式免登录**。我们每次都用"全新独立档案"
       启动浏览器，豆包把它当成一台"从没用过的设备"，游客提问额度每次都重置，
       发一句 + 读回复完全够用 → 整个 demo 可无人值守自动跑完，无需任何人登录。
    2. 豆包输入框是 **contenteditable DIV（role=textbox）**，不是 textarea：
       快照里经常抓不到它，需用 JS 定位"可见的输入条"再操作。
    3. 必须用**真实鼠标点击**聚焦：实测仅 JS `focus()` 后按 Enter 不会发送（豆包忽略），
       用 CDP `Input.dispatchMouseEvent` 在输入条中央点一下、再 Enter 才正常发出。

运行前置：
    1. 用"独立档案 + 调试端口"启动 Edge（新版 Chrome/Edge ≥136 禁止远程调试作用于默认档案）：

         & "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" ^
             --remote-debugging-port=9333 ^
             --remote-allow-origins=* ^
             --user-data-dir="%TEMP%\\edge_db_profile" ^
             "https://www.doubao.com/chat/"

    2. 运行本脚本（默认端口 9333）：

         python examples/browser_doubao_demo.py                     # 发"你好"
         python examples/browser_doubao_demo.py --port 9333 --message "介绍一下你自己"

本脚本验证步骤：先 DOM 确认在对话页（有可编辑输入条，而非登录墙）→ 鼠标点击聚焦 →
清空 → 输入 → 回车前截图确认文字进框 → 回车发送 → 读页面回复文本 + 最终截图。

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


# ---- 浏览器控制器未内置的"坐标点击 / 组合键"小工具（示例内自给，不改动 core） ----
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
    """聚焦的输入框里全选删除（Ctrl+A → Backspace）"""
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
    """读当前聚焦元素里的文本/内容"""
    return b._evaluate(
        "(() => { const e = document.activeElement;"
        " return e ? (e.value !== undefined ? e.value : e.innerText) : ''; })()") or ""


def main():
    ap = argparse.ArgumentParser(description="CDP 驱动豆包(游客)发一条消息并读回复（免登录）")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--url", default="https://www.doubao.com/chat/")
    ap.add_argument("--message", default="你好")
    ap.add_argument("--out", default=None, help="最终截图文件名（默认存本目录）")
    ap.add_argument("--wait-reply", type=float, default=10.0)
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(__file__))
    final_shot = args.out or os.path.join(out_dir, "doubao_demo_result.png")

    b = BrowserController()
    _ = b.attach(port=args.port)
    time.sleep(1.5)
    print("url   =", b._evaluate("location.href"))
    if "doubao.com" not in (b._evaluate("location.href") or ""):
        b.navigate(args.url)
        time.sleep(8.0)
    print("url2  =", b._evaluate("location.href"))

    has_ed = b._evaluate(
        "!!document.querySelector('textarea,[contenteditable=\"true\"],[role=\"textbox\"]')")
    body0 = (b._evaluate("document.body ? document.body.innerText : ''") or "")[:150]
    print("has_editable =", has_ed)
    if not has_ed:
        print("!! 未检测到可编辑输入条，疑似登录墙/引导页。请观察可见窗口。")
        print("body 片段:", repr(body0))
        return 3

    # 1) 真实鼠标点击聚焦输入条（豆包要求，否则 Enter 不发送）
    pos = b._evaluate(_JS_PICK)
    if not pos:
        print("!! 定位输入条失败，中止。")
        return 3
    print("composer_at =", pos)
    mouse_click(b, pos["x"], pos["y"])
    time.sleep(0.6)

    # 2) 清空旧内容 → 输入消息 → 发出前截图确认
    ctrl_a_delete(b)
    time.sleep(0.3)
    b._send("Input.insertText", {"text": args.message})
    time.sleep(0.4)
    typed_shot = os.path.join(tempfile.gettempdir(), "doubao_demo_typed.png")
    print("typed-shot（发出前，应能看到消息在输入条里）=", b.screenshot(typed_shot))

    # 3) 回车发送，读回复
    press_enter(b)
    print("已发送:", args.message)
    time.sleep(args.wait_reply)
    tail = (b._evaluate("document.body ? document.body.innerText : ''") or "")[-1200:]
    print("---- 页面尾部文本（AI 回复区） ----")
    print(tail)

    shot = b.screenshot(final_shot)
    print("final-shot =", shot)

    still = args.message in read_active(b)
    got_reply = ("你好呀" in tail) or ("很高兴" in tail) or ("帮" in tail and args.message in tail)
    if still:
        print("FAIL —— 消息未发出去（输入条里仍有内容）")
        return 1
    print("OK —— 已免登录向豆包发送并读到回复（游客模式）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
