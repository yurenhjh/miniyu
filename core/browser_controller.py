"""
browser_controller.py
第4组：浏览器结构化控制器（基于 CDP / Chrome DevTools Protocol）

在原桌面键鼠控制（app_controller.py）之外，新增“结构化浏览器控制”能力：
不再靠坐标/按键盲操作，而是通过 CDP 直连浏览器内核，读取 DOM 结构、
按语义（索引/选择器）精确操作元素，并拿回结构化结果。

参考设计（开源）：
- Chrome DevTools Protocol：JSON 报文 {"id","method","params"}，
  核心域 Page / Runtime / Input / DOM / Network / Target / Accessibility。
- browser-use：把页面压成“可交互元素索引清单 + 索引动作”，
  规避 LLM 选择器幻觉与“DOM 过大”问题。
- Playwright：“定位器（recipe）而非句柄”“自动等待”是稳定性关键。

设计要点：
- 元素定位用“快照索引”：snapshot() 给可交互元素打 data-agentic-idx 标记，
  之后 click/type 按 index 操作（每次动作前重拍快照，适配 SPA 重渲染）。
- 中文输入走 Input.insertText（CDP 直接向焦点元素插入文本，天然支持中文）。
- 页面操作收敛到三个域：Page（导航/截图）、Runtime（读 DOM/执行 JS）、Input（键入）。
- CDP 跨平台：浏览器屏蔽了操作系统差异，故无需 Windows / Linux 双实现。

依赖：
- 可选第三方库 websocket-client（仅连接浏览器时才需要，测试与联调用 Mock 不需要）。
  安装：pip install websocket-client
"""

import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


# 向可交互元素注入索引标记，并返回压缩后的元素清单（Agent 的“眼睛”）
_SNAPSHOT_JS = r"""
(() => {
  const sel = 'a, button, input, textarea, select, [role="button"], [role="link"], [onclick]';
  const els = Array.from(document.querySelectorAll(sel));
  const out = [];
  els.forEach((el, i) => {
    el.setAttribute('data-agentic-idx', String(i));
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value ||
                  el.getAttribute('aria-label') || el.placeholder || '').toString().trim().slice(0, 60);
    out.push({
      index: i,
      tag: tag,
      role: el.getAttribute('role') || tag,
      type: el.getAttribute('type') || '',
      text: text,
    });
  });
  return out;
})()
"""


class BrowserController:
    """基于 CDP 的浏览器结构化控制器（Chrome / Edge / Chromium）"""

    def __init__(self, ws_url=None, ws=None):
        self.ws_url = ws_url
        self._ws = ws          # 已连接的 WebSocket（可注入，便于测试）
        self._msg_id = 0
        self._events = []
        self._proc = None          # launch() 拉起的浏览器进程（close 时回收，防泄漏）
        self._user_data_dir = None

    # =====================================================
    # 连接生命周期
    # =====================================================

    @property
    def connected(self):
        return self._ws is not None

    def launch(self, port=9222, headless=True, chrome_path=None, user_data_dir=None):
        """
        启动一个带调试端口的 Chrome，并连接到它的页面

        参数：
            port:          调试端口（Chrome 内部 WebSocket 服务监听端口）
            headless:      是否无头模式（后台运行），默认 True
            chrome_path:   浏览器可执行文件路径；未指定时自动探测常用路径
            user_data_dir: 用户数据目录；未指定时用临时目录（干净档案，不泄露登录态）

        返回：
            页面 target 的 WebSocket 地址
        """
        if chrome_path is None:
            chrome_path = self._find_chrome()
        if user_data_dir is None:
            user_data_dir = str(Path(tempfile.mkdtemp(prefix="agentic_chrome_")))
        args = [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
        ]
        if headless:
            args.append("--headless=new")
        args.append("about:blank")
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._user_data_dir = user_data_dir
        ws_url = self._discover_page_ws(port)
        self.connect(ws_url)
        return ws_url

    def attach(self, port=9222):
        """
        连接到一个已在运行、且已开启调试端口的 Chrome 页面

        适用场景：复用用户日常登录态（启动浏览器时带 --remote-debugging-port）。
        """
        ws_url = self._discover_page_ws(port)
        self.connect(ws_url)
        return ws_url

    def connect(self, ws_url):
        """建立到页面 target 的 WebSocket 连接"""
        try:
            import websocket
        except ImportError:
            raise RuntimeError(
                "浏览器结构化控制需要 websocket-client，请先 pip install websocket-client")
        self.ws_url = ws_url
        self._ws = websocket.create_connection(ws_url, timeout=30)
        return self

    def close(self):
        """关闭 WebSocket 连接，并回收 launch() 拉起的浏览器进程与临时目录（防进程泄漏）"""
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None
        self._kill_proc()
        return "浏览器连接已关闭"

    def _kill_proc(self):
        """终止自己拉起的浏览器进程树，并清理临时用户数据目录"""
        if self._proc is not None:
            try:
                if os.name == "nt":
                    # 进程树整体结束（浏览器有大量子进程，光 kill 主进程会留孤儿）
                    subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=8)
                else:
                    self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        if self._user_data_dir:
            try:
                import shutil
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            finally:
                self._user_data_dir = None

    # =====================================================
    # 底层 CDP
    # =====================================================

    def _send(self, method, params=None):
        """发送一条 CDP 命令并同步等待对应 id 的响应（跳过事件）"""
        if self._ws is None:
            raise RuntimeError("浏览器未连接，请先 launch() / attach() / connect()")
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method, "params": params or {}}
        self._ws.send(json.dumps(msg))
        deadline = time.time() + 30
        while time.time() < deadline:
            raw = self._ws.recv()
            if raw is None:
                time.sleep(0.05)
                continue
            data = json.loads(raw)
            if data.get("id") == self._msg_id:
                if "error" in data:
                    raise RuntimeError(
                        f"CDP 错误 {method}: {data['error'].get('message')}")
                return data.get("result", {})
            self._events.append(data)
        raise TimeoutError(f"CDP 命令超时: {method}")

    def _evaluate(self, expression):
        """Runtime.evaluate 便捷封装：执行 JS 并返回其值"""
        result = self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        return result.get("result", {}).get("value")

    # =====================================================
    # 页面操作（结构化）
    # =====================================================

    def navigate(self, url):
        """导航到指定 URL"""
        self._send("Page.navigate", {"url": url})
        time.sleep(1.0)
        return {"url": url, "title": self._evaluate("document.title")}

    def snapshot(self):
        """提取可交互元素索引清单（见模块 _SNAPSHOT_JS）"""
        items = self._evaluate(_SNAPSHOT_JS)
        return items if items is not None else []

    def click(self, index=None, selector=None):
        """点击指定元素（按快照索引或 CSS 选择器）"""
        target = self._locate_expr(index, selector)
        ok = self._evaluate(
            f"(() => {{ const e = {target}; if (!e) return false; "
            f"e.scrollIntoView({{block: 'center'}}); e.click(); return true; }})()")
        if not ok:
            raise LookupError(f"未找到可点击元素: index={index} selector={selector}")
        return {"clicked": True, "index": index, "selector": selector}

    def type_text(self, text, index=None, selector=None):
        """向指定元素输入文本（中文安全，走 CDP Input.insertText）"""
        target = self._locate_expr(index, selector)
        focused = self._evaluate(
            f"(() => {{ const e = {target}; if (!e) return false; e.focus(); return true; }})()")
        if not focused:
            raise LookupError(f"未找到输入元素: index={index} selector={selector}")
        self._send("Input.insertText", {"text": text})
        return {"typed": text, "index": index, "selector": selector}

    def read_text(self, selector=None):
        """读取页面（或指定元素）的文本"""
        if selector:
            value = self._evaluate(
                f"(() => {{ const e = document.querySelector({json.dumps(selector)}); "
                f"return e ? (e.innerText || e.textContent || e.value || '') : null; }})()")
        else:
            value = self._evaluate("document.body ? document.body.innerText : ''")
        if value is None:
            raise LookupError(f"未找到元素: {selector}")
        return value

    def screenshot(self, output=None):
        """对当前页面截图（CDP Page.captureScreenshot）"""
        if output is None:
            output = Path(tempfile.gettempdir()) / f"browser_{int(time.time() * 1000)}.png"
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        result = self._send("Page.captureScreenshot", {"format": "png"})
        data = result.get("data")
        if not data:
            raise RuntimeError("截图失败：未返回图像数据")
        output.write_bytes(base64.b64decode(data))
        return str(output.absolute())

    def wait_for(self, selector=None, text=None, timeout=10):
        """轮询等待某元素出现或文本出现（事件驱动式等待，替代 sleep）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if selector and self._evaluate(
                    f"!!document.querySelector({json.dumps(selector)})"):
                return {"matched": "selector", "selector": selector}
            if text:
                body = self._evaluate("document.body ? document.body.innerText : ''") or ""
                if text in body:
                    return {"matched": "text", "text": text}
            time.sleep(0.3)
        raise TimeoutError(f"等待超时: selector={selector} text={text}")

    def press_enter(self):
        """向当前焦点元素发送回车键（用于提交表单 / 搜索）"""
        for event_type in ("keyDown", "keyUp"):
            self._send("Input.dispatchKeyEvent", {
                "type": event_type,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            })

    # =====================================================
    # 私有辅助
    # =====================================================

    def _locate_expr(self, index=None, selector=None):
        """构造定位目标元素的 JS 表达式"""
        if selector is not None:
            return f"document.querySelector({json.dumps(selector)})"
        if index is not None:
            return f"document.querySelector('[data-agentic-idx=\"{index}\"]')"
        raise ValueError("需要提供 index 或 selector")

    def _discover_page_ws(self, port, retries=20):
        """通过 HTTP /json/list 发现页面 target 的 WebSocket 地址"""
        import urllib.request
        url = f"http://127.0.0.1:{port}/json/list"
        last_err = None
        for _ in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                for t in targets:
                    if t.get("type") == "page":
                        return t["webSocketDebuggerUrl"]
            except Exception as e:
                last_err = e
                time.sleep(0.5)
        raise RuntimeError(f"无法发现浏览器调试 target（port={port}）: {last_err}")

    @staticmethod
    def _find_chrome():
        """在常见路径中寻找 Chrome / Edge / Chromium 可执行文件"""
        import platform
        import shutil
        candidates = []
        if platform.system() == "Windows":
            pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            local = Path(os.environ.get("LOCALAPPDATA", ""))
            candidates = [
                # 优先 Edge：新版 Chrome/Edge ≥136 对默认档案禁用远程调试，且实测 Edge
                # 用独立 user-data-dir + 调试端口 更稳定（见 examples/browser_doubao_demo.py）。
                local / "Microsoft/Edge/Application/msedge.exe",
                pf86 / "Microsoft/Edge/Application/msedge.exe",
                pf / "Microsoft/Edge/Application/msedge.exe",
                local / "Google/Chrome/Application/chrome.exe",
                pf / "Google/Chrome/Application/chrome.exe",
                pf86 / "Google/Chrome/Application/chrome.exe",
            ]
        else:
            candidates = [
                Path("/usr/bin/google-chrome"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
                Path("/usr/bin/microsoft-edge"),
            ]
        for c in candidates:
            if c.exists() or (platform.system() != "Windows" and shutil.which(str(c))):
                return str(c)
        raise RuntimeError("未找到 Chrome/Edge/Chromium，请通过 chrome_path 指定浏览器路径")