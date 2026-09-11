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
import signal
import subprocess
import tempfile
import time
from pathlib import Path


# 向可交互元素注入索引标记并返回压缩元素清单（Agent 的"眼睛"）。
# 目标选择含 contenteditable / role=textbox，并把 contenteditable 富文本框作为一等可输入目标。
# 输出保留 index 兼容旧调用，同时提供 ref / role / name / editable 供模型优先用 ref 定位。
_SNAPSHOT_JS = r"""
(() => {
  const sel = 'a, button, input, textarea, select, [role="button"], [role="link"], ' +
              '[role="textbox"], [role="searchbox"], [onclick], [contenteditable]';
  const els = Array.from(document.querySelectorAll(sel));
  const stableId = (el) => {
    if (el.id) return 'id:' + el.id;
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1) {
      const p = n.parentElement;
      if (!p) break;
      const same = Array.from(p.children).filter((c) => c.tagName === n.tagName);
      const pos = same.indexOf(n);
      parts.unshift(n.tagName.toLowerCase() + ':' + pos);
      n = p;
    }
    return (parts.join('/') || el.tagName.toLowerCase()).slice(0, 80);
  };
  const isEditable = (el) => el.isContentEditable === true ||
    ['input', 'textarea', 'select'].indexOf(el.tagName.toLowerCase()) >= 0;
  const nameOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') ||
    el.getAttribute('title') || el.getAttribute('placeholder') ||
    el.getAttribute('aria-placeholder') || el.getAttribute('data-placeholder') || '').toString().trim();
  const out = [];
  els.forEach((el, i) => {
    el.setAttribute('data-agentic-idx', String(i));
    const sid = stableId(el).replace(/[^A-Za-z0-9_:\-]/g, '_');
    const ref = 'e' + sid;
    el.setAttribute('data-miniyu-ref', ref);
    const tag = el.tagName.toLowerCase();
    const rect = el.getBoundingClientRect();
    out.push({
      ref: ref,
      index: i,
      role: el.getAttribute('role') || tag,
      tag: tag,
      name: nameOf(el).slice(0, 60),
      editable: isEditable(el),
      disabled: el.disabled === true,
    });
  });
  return out;
})()
"""


class SoMStaleError(LookupError):
    """SoM 视觉会话已失效（页面导航 / DOM 大规模变化 / 目标已重排）。
    调用方应返回明确错误，迫使 Agent 重新 browser_inspect 而非猜测旧编号。"""


# 提取"当前视口内可见可交互元素"，并给每个元素打上稳定 ref 标记。
# ref 由元素 id / 结构路径签名生成（不用数组下标 → DOM 重排不会飘），
# 主 frame 暂只扫 document；iframe 前缀为 P6 预留，数据结构已含 frame_id。
_INSPECT_JS = r"""
(() => {
  const sel = 'a[href], button, input, textarea, select, [role="button"], [role="link"], ' +
              '[role="tab"], [role="checkbox"], [role="radio"], [contenteditable], [onclick], [tabindex]';
  const els = Array.from(document.querySelectorAll(sel));
  const vw = window.innerWidth, vh = window.innerHeight;
  const stableId = (el) => {
    if (el.id) return 'id:' + el.id;
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1) {
      const p = n.parentElement;
      if (!p) break;
      const same = Array.from(p.children).filter((c) => c.tagName === n.tagName);
      const pos = same.indexOf(n);
      parts.unshift(n.tagName.toLowerCase() + ':' + pos);
      n = p;
    }
    return (parts.join('/') || el.tagName.toLowerCase()).slice(0, 80);
  };
  const out = [];
  els.forEach((el) => {
    const tag = el.tagName.toLowerCase();
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;              // 尺寸为 0 / 隐藏
    if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw) return; // 移出视口
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return;
    const sid = stableId(el).replace(/[^A-Za-z0-9_:\-]/g, '_');
    const ref = 'e' + sid;
    el.setAttribute('data-miniyu-ref', ref);
    const txt = (el.innerText || el.value || el.getAttribute('aria-label') ||
                 el.getAttribute('title') || el.placeholder || '').toString().trim();
    const role = el.getAttribute('role') || tag;
    out.push({
      ref: ref,
      tag: tag,
      role: role,
      name: txt.slice(0, 80),
      disabled: el.disabled === true,
      onclick: !!el.onclick,
      editable: !!el.getAttribute('contenteditable'),
      tabindex: el.getAttribute('tabindex'),
      x: rect.x, y: rect.y, w: rect.width, h: rect.height,
    });
  });
  return out;
})()
"""


# browser_find：按目标文字/名称/角色/标签"局部搜索"，返回带 ref 的元素清单。
# 对齐 System Prompt 规则文档 Level 1：已知目标文字或名称 → 优先 browser_find，
# 大页面只需找某个按钮/链接/输入框时，不要请求整个页面结构。ref 复用同一套
# stableId 方案并写 data-miniyu-ref，保证查到的 ref 能直接用于 click/type。
def _build_find_js(text, role, tag, only_selector, limit):
    import json as _json
    return r"""
(() => {
  const query = %(query)s;
  const wantRole = %(role)s;
  const wantTag = %(tag)s;
  const onlySel = %(selector)s;
  const limit = %(limit)d;
  const stableId = (el) => {
    if (el.id) return 'id:' + el.id;
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1) {
      const p = n.parentElement;
      if (!p) break;
      const same = Array.from(p.children).filter((c) => c.tagName === n.tagName);
      const pos = same.indexOf(n);
      parts.unshift(n.tagName.toLowerCase() + ':' + pos);
      n = p;
    }
    return (parts.join('/') || el.tagName.toLowerCase()).slice(0, 80);
  };
  const textOf = (el) => (el.innerText || el.value || el.getAttribute('aria-label') ||
    el.getAttribute('title') || el.getAttribute('placeholder') ||
    el.getAttribute('aria-placeholder') || el.getAttribute('data-placeholder') ||
    el.getAttribute('alt') || '').toString().trim();
  const roleOf = (el) => el.getAttribute('role') || el.tagName.toLowerCase();
  const matches = (el) => {
    if (onlySel && !el.matches(onlySel)) return false;
    const tag = el.tagName.toLowerCase();
    if (wantTag && tag !== wantTag) return false;
    let roleOk = true;
    if (wantRole) {
      const rl = wantRole.toLowerCase();
      roleOk = roleOf(el).toLowerCase() === rl;
      // 语义别名：可访问性树里 input/textarea 的 role 是 textbox/searchbox，
      // contenteditable 富文本框（如豆包聊天框）也视为 textbox；button/link/checkbox/radio 放宽到标签判断。
      if (!roleOk && (rl === 'textbox' || rl === 'searchbox')) roleOk = (tag === 'input' || tag === 'textarea' || el.isContentEditable === true);
      if (!roleOk && rl === 'button') roleOk = (tag === 'button');
      if (!roleOk && rl === 'link') roleOk = el.matches('a[href]');
      if (!roleOk && rl === 'checkbox') roleOk = (tag === 'input' && (el.getAttribute('type') === 'checkbox'));
      if (!roleOk && rl === 'radio') roleOk = (tag === 'input' && (el.getAttribute('type') === 'radio'));
    }
    if (!roleOk) return false;
    if (query) {
      const hay = (textOf(el) + ' ' + roleOf(el)).toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  };
  const sel = 'a[href], button, input, textarea, select, [role], [onclick], ' +
              '[tabindex], [contenteditable]';
  const scope = onlySel ? document.querySelectorAll(onlySel)
                        : document.querySelectorAll(sel);
  const out = [];
  for (const el of scope) {
    if (out.length >= limit) break;
    if (!matches(el)) continue;
    const sid = stableId(el).replace(/[^A-Za-z0-9_:\-]/g, '_');
    const ref = 'e' + sid;
    el.setAttribute('data-miniyu-ref', ref);
    const r = el.getBoundingClientRect();
    out.push({
      ref: ref,
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      name: textOf(el).slice(0, 80),
      editable: el.isContentEditable === true ||
        ['input', 'textarea', 'select'].indexOf(el.tagName.toLowerCase()) >= 0,
      disabled: el.disabled === true,
      in_viewport: r.width > 2 && r.height > 2 &&
        r.bottom >= 0 && r.top <= window.innerHeight &&
        r.right >= 0 && r.left <= window.innerWidth,
    });
  }
  return out;
})()
""" % {
        "query": _json.dumps((text or "").strip().lower()),
        "role": _json.dumps(role or None),
        "tag": _json.dumps((tag or "").strip().lower() or None),
        "selector": _json.dumps(only_selector or None),
        "limit": max(1, min(int(limit or 20), 100)),
    }


def _target_key(t):
    """交互元素优先级排序键（值越小越优先）。对齐方案第 18~19 节：
    button/input/select/textarea 最优先 → role=button/link/tab 次之 → 链接 → 可编辑 → 其他。"""
    tag = (t.tag or "").lower()
    role = (t.role or "").lower()
    if tag in ("button", "input", "select", "textarea"):
        return 0
    if role in ("button", "link", "tab", "checkbox", "radio"):
        return 1
    if tag == "a":
        return 2
    if t.extra.get("editable"):
        return 3
    if t.extra.get("onclick"):
        return 4
    if t.extra.get("tabindex") is not None:
        return 5
    return 9


class BrowserController:
    """基于 CDP 的浏览器结构化控制器（Chrome / Edge / Chromium）"""

    def __init__(self, ws_url=None, ws=None):
        self.ws_url = ws_url
        self._ws = ws          # 已连接的 WebSocket（可注入，便于测试）
        self._msg_id = 0
        self._events = []
        self._proc = None          # launch() 拉起的浏览器进程（close 时回收，防泄漏）
        self._user_data_dir = None
        self._temp_user_data_dir = True  # True=临时档案（close 时删除）；False=持久档案（保留登录态）

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
        if user_data_dir:
            # 持久化档案：保留登录态（豆包/QQ）免限流；不随 close 删除
            user_data_dir = os.path.expandvars(str(user_data_dir))
            user_data_dir = str(Path(user_data_dir).expanduser())
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            self._temp_user_data_dir = False
        else:
            user_data_dir = str(Path(tempfile.mkdtemp(prefix="agentic_chrome_")))
            self._temp_user_data_dir = True
        args = [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-service-autorun",
            "--disable-sync",
            "--hide-crash-restore-bubble",
            "--remote-allow-origins=*",
        ]
        if headless:
            args.append("--headless=new")
        args.append("about:blank")
        # 非 Windows 让浏览器自成进程组，便于 _kill_proc 用 killpg 整树结束，
        # 避免只杀主进程而残留渲染子进程（会锁端口/档案，下次 launch 受扰）。
        popen_kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(args, **popen_kwargs)
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
                    # 整进程组结束（launch 时已 start_new_session），
                    # 否则只杀主进程会残留渲染子进程，锁住端口/档案。
                    try:
                        os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                    except Exception:
                        self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        if self._user_data_dir and self._temp_user_data_dir:
            # 仅删除临时档案；持久档案（如用户填的 user_data_dir）保留登录态
            try:
                import shutil
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            except Exception:
                pass
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

    def find(self, text=None, role=None, tag=None, selector=None, max_results=20):
        """按目标文字/名称/角色/标签局部搜索元素（browser_find，Level 1）。

        text=    要匹配的文字/名称（子串、大小写不敏感；空则不按文字过滤）
        role=    只返回指定 role 的元素
        tag=     只返回指定标签的元素（如 button / a / input）
        selector=  CSS 选择器限定扫描范围（更聚焦、更快）
        max_results= 最多返回条数（默认 20，上限 100）

        返回的每条都带稳定 ref（已写入 data-miniyu-ref，可直接给 click/type 用），
        以及 in_viewport 标记——大页面 "先 find 缩小范围、再按 ref 操作"。
        """
        js = _build_find_js(text, role, tag, selector, max_results)
        items = self._evaluate(js)
        return items if isinstance(items, list) else []

    def click(self, target=None, index=None, selector=None, x=None, y=None):
        """点击元素。支持统一 target（ref / som:N / css 选择器），也为旧调用保留
        index / selector / x / y 三套参数（向后兼容 57 工具中的既有用法）。

        - index / selector：走 DOM 的 el.click()（快照下标或 CSS 选择器）
        - x / y：最近一次截图上的【图片像素】坐标 → CDP 真实鼠标点击
        - target='som:N'：按当前 InspectSession 的视觉编号解析成 ref，
          再对实时定位的元素中心做真实鼠标点击；会话失效抛 SoMStaleError。

        真实鼠标（相对 DOM .click()）对 input/textarea/contenteditable 等忽略合成
        事件的页面更可信（如豆包输入条必须先真实点一下聚焦）。
        """
        from core.uitarget import is_ref, parse_som
        if x is not None and y is not None:
            return self.mouse_click(int(x), int(y))
        if target is not None:
            som = parse_som(target)
            if som is not None:
                tgt = self._som_lookup(som)
                if tgt is None or not tgt.ref:
                    raise SoMStaleError(
                        f"SoM 会话已失效（页面可能已导航/变化），编号 {som} 无法使用，"
                        f"请重新调用 browser_inspect 后再点击")
                ref = tgt.ref
            elif is_ref(target):
                ref = target
            else:
                # 其余视为 CSS 选择器（兼容 'css:...' 前缀）
                return self._click_legacy(None, target.lstrip("css:"))
            center = self._resolve_ref_center(ref)
            if not center:
                raise SoMStaleError(
                    f"目标 {ref} 已不在页面中（可能导航或重排），请重新 browser_inspect")
            dpr = self._dpr()
            return dict(self.mouse_click(int(center[0] * dpr), int(center[1] * dpr)),
                        ref=ref, method="som")
        return self._click_legacy(index, selector)

    def _click_legacy(self, index=None, selector=None):
        """旧式 DOM 语义点击（el.click()）——按快照下标或 CSS 选择器。"""
        tgt = self._locate_expr(index, selector)
        ok = self._evaluate(
            f"(() => {{ const e = {tgt}; if (!e) return false; "
            f"e.scrollIntoView({{block: 'center'}}); e.click(); return true; }})()")
        if not ok:
            raise LookupError(f"未找到可点击元素: index={index} selector={selector}")
        return {"clicked": True, "index": index, "selector": selector, "method": "dom"}

    def type_text(self, text, target=None, index=None, selector=None):
        """向指定元素输入文本（中文安全，走 CDP Input.insertText）。

        - target='som:N' / ref：按当前 InspectSession 的 ref 实时聚焦元素再输入；
        - index / selector：旧式 DOM 定位（向后兼容）。
        """
        if target is not None:
            from core.uitarget import is_ref, parse_som
            som = parse_som(target)
            if som is not None:
                tgt = self._som_lookup(som)
                if tgt is None or not tgt.ref:
                    raise SoMStaleError(
                        f"SoM 会话已失效，编号 {som} 无法使用，请重新 browser_inspect")
                ref = tgt.ref
            elif is_ref(target):
                ref = target
            else:
                return self._type_legacy(text, None, target.lstrip("css:"))
            return self._type_by_ref(text, ref)
        return self._type_legacy(text, index, selector)

    def _type_by_ref(self, text, ref):
        """按 ref 实时定位并聚焦输入元素，再插入文本。

        对 readonly / disabled / contenteditable=false 的非可编辑目标拒绝输入（P2-1）。
        """
        attr = f'[data-miniyu-ref="{ref}"]'
        state = self._evaluate(
            f"(() => {{ const e = document.querySelector({json.dumps(attr)}); "
            f"if (!e) return {{ok: false, reason: 'gone'}}; "
            f"if (e.disabled === true) return {{ok: false, reason: 'disabled'}}; "
            f"if (e.readOnly === true) return {{ok: false, reason: 'readonly'}}; "
            f"const isEdt = e.isContentEditable === true || "
            f"['input','textarea','select'].indexOf(e.tagName.toLowerCase()) >= 0; "
            f"if (!isEdt) return {{ok: false, reason: 'not_editable'}}; "
            f"e.scrollIntoView({{block: 'center'}}); e.focus(); return {{ok: true}}; }})()")
        # 兼容旧桩返回 bool（True=聚焦成功，False=目标缺失）
        if state is True or (isinstance(state, dict) and state.get("ok")):
            self._send("Input.insertText", {"text": text})
            return {"typed": text, "ref": ref, "method": "som"}
        reason = state.get("reason") if isinstance(state, dict) else "gone"
        if reason in ("disabled", "readonly", "not_editable"):
            raise LookupError(f"目标 {ref} 不可输入（{reason}），请重新 browser_inspect 选择可编辑元素")
        raise SoMStaleError(f"输入目标 {ref} 已不在页面中，请重新 browser_inspect")

    def _type_legacy(self, text, index=None, selector=None):
        """旧式 DOM 定位输入。"""
        target = self._locate_expr(index, selector)
        focused = self._evaluate(
            f"(() => {{ const e = {target}; if (!e) return false; e.focus(); return true; }})()")
        if not focused:
            raise LookupError(f"未找到输入元素: index={index} selector={selector}")
        self._send("Input.insertText", {"text": text})
        return {"typed": text, "index": index, "selector": selector, "method": "dom"}

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

    def refresh(self, ignore_cache=True):
        """刷新当前页面（SPA 加载空白 / 等待后的兜底恢复手段）。

        Page.reload 后页面可能仍在渲染，内部空转一小段再回读 URL/title，
        供上层判断是否真正到达目标页。
        """
        self._send("Page.reload", {"ignoreCache": bool(ignore_cache)})
        time.sleep(1.2)
        return {
            "url": self._evaluate("location.href") or "",
            "title": self._evaluate("document.title") or "",
        }

    def wait_for(self, selector=None, text=None, timeout=10):
        """轮询等待某元素出现或文本出现（事件驱动式等待，替代 sleep）"""
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 10.0
        if timeout <= 0:
            timeout = 10.0
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

    def mouse_click(self, x, y, button="left", click_count=1):
        """坐标级【真实】鼠标点击（CDP Input.dispatchMouseEvent，浏览器级可信）。

        与 click()（DOM 的 el.click()，JS 合成事件）不同：这里派发的是真实鼠标设备
        事件，对 input/textarea/contenteditable 等忽略 DOM .click() 的页面有效
        （典型：豆包聊天输入框必须先真实点一下聚焦，再输入+回车才能发出消息）。

        参数：
            x, y:        最近一次 screenshot() 截图上的【图片像素】坐标（含系统缩放）。
                         内部按 window.devicePixelRatio 折算成 CSS 视口坐标再派发，
                         适配高分屏 / Windows 缩放，保证"截图看到哪里就点到哪里"。
            button:      左键 left / 右键 right / 中键 middle，默认 left
            click_count: 点击次数（双击传 2），默认 1

        返回：
            {"clicked": True, "x", "y", "button"}
        """
        dpr = float(self._evaluate("window.devicePixelRatio") or 1) or 1
        for event_type in ("mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": x / dpr,
                "y": y / dpr,
                "button": button,
                "clickCount": click_count,
            })
        return {"clicked": True, "x": x, "y": y, "button": button}

    # =====================================================
    # SoM 视觉定位（browser_inspect）——DOM-SoM-坐标混合架构 P3
    # =====================================================

    def _dpr(self) -> float:
        return float(self._evaluate("window.devicePixelRatio") or 1) or 1

    def _get_accessible_elements(self) -> list:
        """Runtime 提取当前视口内可交互元素（含 ref / CSS bbox / 文本）。"""
        items = self._evaluate(_INSPECT_JS)
        return items if isinstance(items, list) else []

    def inspect_elements(self, max_elements: int = 40) -> list:
        """提取 → 交互优先级排序 → 截断 → 生成带视觉编号与 ref 的 UITarget 列表。

        返回的 visual_num 只是「截图上的编号圈」（视觉标签，见方案第 5 节），
        真正用于执行的是 ref 字段（稳定身份，DOM 重排不漂移）。
        """
        from core.uitarget import UITarget
        raw = self._get_accessible_elements()
        dpr = self._dpr()
        targets = [UITarget.from_js(it, dpr=dpr, source="browser_som") for it in raw]
        targets.sort(key=_target_key)
        for i, t in enumerate(targets[:max_elements], start=1):
            t.visual_num = i
        return targets[:max_elements]

    def annotated_screenshot(self, output=None, max_elements: int = 40, question: str = ""):
        """截"带编号覆盖层"的页面图：Page.captureScreenshot + DOM 取框 + PIL 画编号。

        等价于内建浏览器「截图可交互元素并按编号标注」：模型看图看到 ① ② … ③，
        选一个编号让 `click(target='som:N')` 落点。返回：
            {image_path, url, title, dpr, elements:[{num,ref,role,name,tag,bbox_css,center_css}...]}
        同时建立本控制器上【唯一的】InspectSession（num→ref 映射），供 som: 目标解析。
        页面一旦导航 / 明显变化，旧 session 由 _resolve_ref 判定失效。
        """
        from PIL import Image, ImageDraw, ImageFont
        from core.uitarget import UITarget, bbox_to_device
        targets = self.inspect_elements(max_elements=max_elements)
        dpr = self._dpr()
        url = self._evaluate("location.href") or ""
        title = self._evaluate("document.title") or ""

        result = self._send("Page.captureScreenshot", {"format": "png"})
        data = result.get("data")
        if not data:
            raise RuntimeError("browser_inspect 截图失败：未返回图像数据")
        import io
        img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        for t in targets:
            _d = bbox_to_device(t.bbox_css, dpr)
            if not _d:
                continue
            x0, y0, w0, h0 = _d
            draw.rectangle([x0, y0, x0 + w0, y0 + h0], outline=(220, 40, 40), width=2)
            cx, cy = x0, y0
            r = 11
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 40, 40))
            label = str(t.visual_num)
            if font:
                draw.text((cx - r / 2, cy - r / 2 - 3), label, fill=(255, 255, 255), font=font)
            else:
                draw.text((cx - 4, cy - 8), label, fill=(255, 255, 255))

        if output is None:
            output = Path(tempfile.gettempdir()) / f"browser_inspect_{int(time.time() * 1000)}.png"
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        img.save(output)

        # 单一 InspectSession：num→ref 映射 + 状态指纹（供失效判定）
        self._som_session = {
            "url": url,
            "title": title,
            "dom_sig": self._dom_sig(),
            "elements": targets,
            "created": time.time(),
        }
        return {
            "image_path": str(output.absolute()),
            "url": url,
            "title": title,
            "dpr": dpr,
            "elements": [t.to_dict() for t in targets],
        }

    def _dom_sig(self) -> str:
        """当前 DOM 的轻量指纹（URL 之外判断页面是否明显变化，供客观验证）。"""
        try:
            body = self._evaluate("document.body ? document.body.innerHTML.length : 0")
        except Exception:
            body = 0
        try:
            href = self._evaluate("location.href") or ""
        except Exception:
            href = ""
        import hashlib
        return hashlib.sha1(f"{float(body)}|{href}".encode("utf-8")).hexdigest()[:12]

    def som_session_valid(self) -> bool:
        """当前浏览状态是否仍与最近一次 browser_inspect 会话一致。"""
        sess = getattr(self, "_som_session", None)
        if not sess:
            return False
        try:
            url = self._evaluate("location.href") or ""
            sig = self._dom_sig()
        except Exception:
            return False
        return url == sess["url"] and sig == sess["dom_sig"]

    def _som_lookup(self, num: int):
        """在最近一次 InspectSession 中按视觉编号取 UITarget；会话失效返回 None。"""
        sess = getattr(self, "_som_session", None)
        if not sess or not self.som_session_valid():
            return None
        for t in sess["elements"]:
            if t.visual_num == num:
                return t
        return None

    def _live_ref_exists(self, ref: str) -> bool:
        """校验 ref 指向的元素此刻仍在 DOM（避免用过期的会话编号点击）。"""
        if not ref:
            return False
        attr = f'[data-miniyu-ref="{ref}"]'
        return bool(self._evaluate(f"!!document.querySelector({json.dumps(attr)})"))

    def _resolve_ref_center(self, ref: str):
        """按 ref 实时定位元素，返回 CSS 视口中心 [cx, cy]；元素已不存在返回 None。

        视觉只负责“找”，真正执行落到当前 DOM 的 ref 元素上（方案第 9~10 节）。
        先 scrollIntoView 再取中心：browser_find / 滚动后的 SoM 元素即使不在当前视口，
        也能正确滚动到可视区后做真实鼠标点击。
        """
        attr = f'[data-miniyu-ref="{ref}"]'
        return self._evaluate(
            f"(() => {{ const e = document.querySelector({json.dumps(attr)}); if (!e) return null; "
            f"e.scrollIntoView({{block: 'center'}}); const r = e.getBoundingClientRect(); "
            f"return [r.x + r.width / 2, r.y + r.height / 2]; }})()"
        )

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
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/microsoft-edge",
                "/usr/bin/microsoft-edge-stable",
                "/snap/bin/chromium",            # snap 版
                "/usr/local/bin/chromium",        # 自编译/包管理器自装
                "/opt/google/chrome/chrome",
            ]
        for c in candidates:
            p = Path(c) if not isinstance(c, Path) else c
            if p.exists() or (platform.system() != "Windows" and shutil.which(str(c))):
                return str(c)
        raise RuntimeError(
            "未找到 Chrome/Edge/Chromium，请通过 chrome_path 指定浏览器路径")