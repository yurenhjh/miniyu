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
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path


# 向可交互元素注入索引标记并返回压缩元素清单（Agent 的"眼睛"）。
# 目标选择含 contenteditable / role=textbox，并把 contenteditable 富文本框作为一等可输入目标。
# 输出保留 index 兼容旧调用，同时提供 ref / role / name / editable 供模型优先用 ref 定位。
# P2-4：统一的"真正可输入宿主"解析 JS 助手（仅命名空间 _miniyu*），供 find 下钻与 browser_type
# 内部 recovery 共用。核心不变式：semantic wrapper（如 div role=textbox）可能自身不可输入，
# 真正的编辑宿主是内部子节点；handle 必须收敛到真实宿主，而不能挂在 wrapper 上。
_EDIT_HOST_HELPERS_JS = r"""
  const _miniyuTypingInput = (el) => {
    if (el.tagName.toLowerCase() !== 'input') return false;
    return !['hidden', 'file', 'submit', 'reset', 'button', 'checkbox', 'radio']
      .includes((el.getAttribute('type') || '').toLowerCase());
  };
  const _miniyuVisible = (el) => {
    const _cs = getComputedStyle(el);
    const _r = el.getBoundingClientRect();
    return _cs.display !== 'none' && _cs.visibility !== 'hidden' && _r.width > 1 && _r.height > 1;
  };
  const _miniyuEditableTag = (el) => {
    const _t = el.tagName.toLowerCase();
    if (_t === 'textarea') return true;
    if (_t === 'input') return _miniyuTypingInput(el);
    if (el.isContentEditable === true) return true;
    return false;
  };
  const _miniyuUsableHost = (el) => {
    if (!el || el.nodeType !== 1 || el.disabled === true) return null;
    if (el.readOnly === true) return null;
    if (!_miniyuEditableTag(el) || !_miniyuVisible(el)) return null;
    return el;
  };
  const _miniyuResolveHost = (root) => {
    if (!root || root.nodeType !== 1) return null;
    const _self = _miniyuUsableHost(root);
    if (_self) return _self;                       // 自身即可编辑，直接返回自身
    // 固定优先级（P2-4）：wrapper 内先找 textarea → 再 input → 最后 contenteditable，
    // 不能只看 document 顺序，否则富文本 container 里的首个节点会错误抢占。
    const _ta = root.querySelector('textarea');
    if (_ta) { const _h = _miniyuUsableHost(_ta); if (_h) return _h; }
    const _inp = root.querySelector('input');
    if (_inp) { const _h = _miniyuUsableHost(_inp); if (_h) return _h; }
    const _ce = root.querySelector('[contenteditable="true"]');
    if (_ce) { const _h = _miniyuUsableHost(_ce); if (_h) return _h; }
    // 兜底：兼容 contenteditable="" / "plaintext-only" 等非标准写法，全量扫
    const _all = root.querySelectorAll('textarea, input, [contenteditable]');
    for (const _el of _all) { const _h = _miniyuUsableHost(_el); if (_h) return _h; }
    return null;
  };
  const _miniyuStableId = (el) => {
    if (el.id) return 'id:' + el.id;
    const _p = [];
    let _n = el;
    while (_n && _n.nodeType === 1) {
      const _par = _n.parentElement;
      if (!_par) break;
      const _same = Array.from(_par.children).filter((c) => c.tagName === _n.tagName);
      const _pos = _same.indexOf(_n);
      _p.unshift(_n.tagName.toLowerCase() + ':' + _pos);
      _n = _par;
    }
    return (_p.join('/') || el.tagName.toLowerCase()).slice(0, 80);
  };
"""

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
  // 可输入判断必须与 _type_by_ref / find 完全一致：只有原生可输入控件或真正
  // contenteditable 才算 editable；role=textbox 的 wrapper 或隐藏控件不算。
  const _typingInput = (el) => {
    if (el.tagName.toLowerCase() !== 'input') return false;
    return !['hidden', 'file', 'submit', 'reset', 'button', 'checkbox', 'radio']
      .includes((el.getAttribute('type') || '').toLowerCase());
  };
  const isEditable = (el) => {
    const t = el.tagName.toLowerCase();
    if (t === 'input') return _typingInput(el);
    return t === 'textarea' || t === 'select' || el.isContentEditable === true;
  };
  const inputKind = (el) => {
    const t = el.tagName.toLowerCase();
    const ty = (el.getAttribute('type') || '').toLowerCase();
    if (t === 'input') return _typingInput(el) ? 'native-input' : 'hidden-input';
    if (t === 'textarea') return 'textarea';
    if (t === 'select') return 'select';
    if (el.isContentEditable === true) return 'contenteditable';
    const r = (el.getAttribute('role') || '').toLowerCase();
    return (r === 'textbox' || r === 'searchbox') ? 'semantic-textbox' : 'other';
  };
  const visibleOf = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 1 && r.height > 1;
  };
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
      input_kind: inputKind(el),
      visible: visibleOf(el),
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
    return "(() => {" + _EDIT_HOST_HELPERS_JS + r"""
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
  // 与 _SNAPSHOT_JS / _type_by_ref 同一套可输入判断：非输入型 input（hidden/file/checkbox…）与
  // 仅 role=textbox 的 wrapper 都不算可编辑。role 别名也不再把这类 input 当 textbox 候选。
  const _typingInput = (el) => {
    if (el.tagName.toLowerCase() !== 'input') return false;
    return !['hidden', 'file', 'submit', 'reset', 'button', 'checkbox', 'radio']
      .includes((el.getAttribute('type') || '').toLowerCase());
  };
  const isEditable = (el) => {
    const t = el.tagName.toLowerCase();
    if (t === 'input') return _typingInput(el);
    return t === 'textarea' || t === 'select' || el.isContentEditable === true;
  };
  const inputKind = (el) => {
    const t = el.tagName.toLowerCase();
    if (t === 'input') return _typingInput(el) ? 'native-input' : 'hidden-input';
    if (t === 'textarea') return 'textarea';
    if (t === 'select') return 'select';
    if (el.isContentEditable === true) return 'contenteditable';
    const r = (el.getAttribute('role') || '').toLowerCase();
    return (r === 'textbox' || r === 'searchbox') ? 'semantic-textbox' : 'other';
  };
  const visibleOf = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 1 && r.height > 1;
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
      // 非输入型 input（type=hidden/file/…) 不视为 textbox 候选，避免把隐藏上传框当成聊天输入框。
      if (!roleOk && (rl === 'textbox' || rl === 'searchbox'))
        roleOk = (tag === 'textarea') ||
                 (tag === 'input' && _typingInput(el)) ||
                 el.isContentEditable === true;
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
    // P2-4：semantic target 可能只是 wrapper，真实编辑宿主在其内部子节点。
    // 统一解析到真正可输入的 host，handle/ref 指向 host，而不是 wrapper。
    const host = _miniyuResolveHost(el);
    const use = host || el;
    const sid = stableId(use).replace(/[^A-Za-z0-9_:\-]/g, '_');
    const ref = 'e' + sid;
    use.setAttribute('data-miniyu-ref', ref);
    const r = use.getBoundingClientRect();
    out.push({
      ref: ref,
      tag: use.tagName.toLowerCase(),
      role: roleOf(el) || roleOf(use),
      name: (textOf(use) || textOf(el)).slice(0, 80),
      editable: !!host || isEditable(el),
      input_kind: inputKind(use),
      visible: visibleOf(use),
      disabled: use.disabled === true,
      // P2-4.1：edit_host 表示"该元素是否就是真正的输入宿主"（自身或下钻皆算）。
      // host_kind 区分是自身（self）还是 wrapper 内部下钻（descendant）；无宿主为 null。
      edit_host: !!host,
      host_kind: host ? (use === el ? 'self' : 'descendant') : null,
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


def _front_handle(item):
    """把 dict 的 handle 提到最前，返回新 dict（不原地改动）。

    handle 是给模型用的短句柄，必须出现在结果最前面；超长内部 ref 落到后面，
    即使日志截断也只会切掉 ref/尾部字段，而不会把 handle 吞掉。
    """
    if not isinstance(item, dict) or "handle" not in item:
        return item
    d = {"handle": item["handle"]}
    for k, v in item.items():
        if k != "handle":
            d[k] = v
    return d


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


def _target_rank(el):
    """browser_find 候选排序分值（P2-2，值越大越优先）。

    目标：可见 + 可输入 + in_viewport 优先于隐藏 / 不可编辑；隐藏 input 与
    不可编辑的 wrapper 排最末。与 _type_by_ref 的可输入判断保持一致。
    """
    kind = (el.get("input_kind") or el.get("tag") or "").lstrip().lower()
    score = 0
    if kind in ("native-input", "textarea"):
        score += 100
    elif kind == "contenteditable":
        score += 80
    elif kind == "select":
        score += 70
    elif kind == "semantic-textbox":
        score += 40
    elif kind == "hidden-input":
        score -= 120
    if el.get("in_viewport"):
        score += 25
    if el.get("visible"):
        score += 30
    else:
        score -= 60
    if el.get("disabled"):
        score -= 120
    if not el.get("editable"):
        score -= 80          # 不可编辑目标显著降权
    return score


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

        # P2-3 Target Handle 层：模型只拿短 handle（e1/e2…），内部才映射长 internal ref。
        # handle 绑定当前 observation/session（页面导航或 DOM 明显变化即失效，须重新 find/snapshot）。
        self._handle_seq = 0            # 全局递增，保证跨 session 不撞号
        self._handle_registry = {}      # handle -> {"ref": 长internal_ref, "sid": session_id, "sig": 页面指纹}
        self._activity_sid = 0          # 当前活动 session 号
        self._page_sig = None           # (url, dom_sig)，用于判断页面是否变化新建 session

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

    # =====================================================
    # P2-3 Target Handle 层：短 handle ↔ 长 internal ref ↔ 当前页面 session
    # =====================================================

    def _read_page_sig(self):
        """读取当前页面的轻量指纹 (url, dom_sig)；读不到时返回 None（无法判定变化）。"""
        try:
            url = self._evaluate("location.href") or ""
        except Exception:
            url = ""
        try:
            sig = self._dom_sig()
        except Exception:
            sig = None
        if url == "" and sig is None:
            return None
        return (url, sig)

    def _alloc_handles(self, items):
        """为元素清单（list[dict]，含 ref）分配短 handle 并登记到 registry；返回附了 handle 的清单。

        页面指纹与最近一次采样不同 → 新建 session，旧 handle 全部失效（由 _handle_to_ref 拒绝）。
        每个元素都带稳定内部 ref 时才会分配；无 ref 的条目原样保留。
        """
        sig = self._read_page_sig()
        if sig is not None and sig != self._page_sig:
            self._activity_sid += 1
            self._page_sig = sig
        for it in items:
            if not isinstance(it, dict) or not it.get("ref"):
                continue
            internal_ref = it["ref"]
            existing = next(
                (h for h, r in self._handle_registry.items()
                 if r["ref"] == internal_ref and r["sid"] == self._activity_sid),
                None)
            if existing is None:
                self._handle_seq += 1
                handle = f"e{self._handle_seq}"
                self._handle_registry[handle] = {
                    "ref": internal_ref, "sid": self._activity_sid, "sig": sig}
            else:
                # 同一会话内该 ref 已登记过（如 snapshot 先登记、find 再命中）：
                # 必须把既有 handle 回填，否则模型拿不到可用 handle，会绕回 selector 旧路。
                handle = existing
            it["handle"] = handle
        # 让 handle 恒排最前：模型优先看到短句柄，超长内部 ref 落在后面，不会被截断吞掉。
        return [_front_handle(it) for it in items]

    def _handle_to_ref(self, target):
        """把短 handle 解析成其登记的 internal ref；不是 handle 或已失效返回 None。

        失效判定：页面指纹与 handle 登记时不一致 → stale（页面已导航/变化），
        必须重新 find/snapshot/inspect 拿新 handle。
        """
        if not isinstance(target, str) or target not in self._handle_registry:
            return None
        entry = self._handle_registry[target]
        cur = self._read_page_sig()
        if cur is None:
            ok = entry["sid"] == self._activity_sid
        else:
            ok = cur == entry["sig"]
        return entry["ref"] if ok else None

    def snapshot(self):
        """提取可交互元素索引清单（见模块 _SNAPSHOT_JS）。每条带 ref 时附加短 handle，供模型首选。"""
        items = self._evaluate(_SNAPSHOT_JS)
        if not isinstance(items, list):
            return []
        return self._alloc_handles([dict(x) for x in items])

    def find(self, text=None, role=None, tag=None, selector=None, max_results=20):
        """按目标文字/名称/角色/标签局部搜索元素（browser_find，Level 1）。

        text=    要匹配的文字/名称（子串、大小写不敏感；空则不按文字过滤）
        role=    只返回指定 role 的元素
        tag=     只返回指定标签的元素（如 button / a / input）
        selector=  CSS 选择器限定扫描范围（更聚焦、更快）
        max_results= 最多返回条数（默认 20，上限 100）

        返回的每条都带稳定 ref（已写入 data-miniyu-ref，可直接给 click/type 用），
        以及 in_viewport / visible / input_kind 标记——大页面 "先 find 缩小范围、
        再按 ref 操作"。

        P2-2 在工具层做候选排序与过滤，避免把隐藏 input / 不可编辑 wrapper 排在
        可见可输入目标之前：
          - 排序：可见 + 可输入 + in_viewport 优先于隐藏 / 不可编辑；
          - 过滤：查询 textbox/searchbox 且存在可编辑候选时，剔除 "hidden-input"
            与不可编辑的 "semantic-textbox" wrapper，减少模型误判。
        """
        js = _build_find_js(text, role, tag, selector, max_results)
        items = self._evaluate(js)
        if not isinstance(items, list):
            return []
        items = [dict(x) for x in items]
        items.sort(key=_target_rank, reverse=True)   # 稳定降序：高优先级在前
        role_q = (role or "").strip().lower()
        editable_any = any(x.get("editable") for x in items)
        if role_q in ("textbox", "searchbox") and editable_any:
            items = [
                x for x in items
                if not (x.get("input_kind") == "hidden-input")
                and not (x.get("input_kind") == "semantic-textbox" and not x.get("editable"))
            ]
        items = self._alloc_handles(items)
        return items[:max(1, min(int(max_results or 20), 100))]

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
                method = "som"
            elif is_ref(target):
                # 短 handle 优先解析成内部 ref；非 handle 的（如旧式长 ref）保持向后兼容直用
                h = self._handle_to_ref(target)
                ref = h or target
                method = "handle" if h else "dom"
            else:
                # 其余视为 CSS 选择器（兼容 'css:...' 前缀）
                return self._click_legacy(None, target.lstrip("css:"))
            center = self._resolve_ref_center(ref)
            if not center:
                raise SoMStaleError(
                    f"目标 {ref} 已不在页面中（可能导航或重排），请重新 browser_inspect")
            dpr = self._dpr()
            return dict(self.mouse_click(int(center[0] * dpr), int(center[1] * dpr)),
                        ref=ref, method=method)
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
                method = "som"
            elif is_ref(target):
                # 短 handle 优先解析成内部 ref；旧式长 ref 保持向后兼容直用
                h = self._handle_to_ref(target)
                method = "handle" if h else "ref"
                ref = h or target
            else:
                return self._type_legacy(text, None, target.lstrip("css:"))
            # P2-4：type 也支持一次"工具内确定性 recovery"（见 _type_ref_with_recovery）
            ret = self._type_ref_with_recovery(text, ref)
            ret["method"] = method
            return ret
        return self._type_legacy(text, index, selector)

    def _type_ref_with_recovery(self, text, ref):
        """按 ref 输入，内部做一次工具内确定性 recovery（P2-4）。

        当 ref 指向的是 semantic wrapper（自身 browser_type 判定 not_editable）时，
        工具自动把目标下钻到内部真正可输入的 edit host 并重试一次；成功则 typed=true，
        不再把这次确定性失败抛回 LLM 让它多花一个回合去思考。
        """
        try:
            return self._type_by_ref(text, ref)
        except LookupError as e:
            if "not_editable" not in str(e):
                raise
            host_ref = self._resolve_type_host_ref(ref)
            if host_ref and host_ref != ref:
                try:
                    return self._type_by_ref(text, host_ref)
                except LookupError:
                    raise e
            raise

    def _resolve_type_host_ref(self, ref):
        """给定（可能指向 wrapper 的）ref，返回内部真正可输入的 edit host 的新 ref；
        无下钻目标返回 None。把 host 也写入 data-miniyu-ref，供 _type_by_ref 复用。"""
        attr = f'[data-miniyu-ref="{ref}"]'
        js = (
            "(() => {"
            + _EDIT_HOST_HELPERS_JS
            + f"const el = document.querySelector({json.dumps(attr)}); "
            f"if (!el) return null; "
            f"const host = _miniyuResolveHost(el); "
            f"if (!host || host === el) return null; "
            f"const sid = _miniyuStableId(host).replace(/[^A-Za-z0-9_:\\-]/g, '_'); "
            f"const nr = 'e' + sid; host.setAttribute('data-miniyu-ref', nr); return nr; "
            "})()"
        )
        res = self._evaluate(js)
        return res if isinstance(res, str) and res else None

    def _type_by_ref(self, text, ref):
        """按 ref 实时定位并聚焦输入元素，再插入文本。

        可输入判断与 _SNAPSHOT_JS / find 完全一致：只有原生可输入控件或真正
        contenteditable 才允许输入；readonly/disabled/隐藏/非输入型 input/仅
        role=textbox 的 wrapper 一律拒绝。失败时给出 browser_find/snapshot
        恢复建议（P2-2），不引导升级到 SoM。
        """
        attr = f'[data-miniyu-ref="{ref}"]'
        state = self._evaluate(
            f"(() => {{ const e = document.querySelector({json.dumps(attr)}); "
            f"if (!e) return {{ok: false, reason: 'gone'}}; "
            f"if (e.disabled === true) return {{ok: false, reason: 'disabled'}}; "
            f"if (e.readOnly === true) return {{ok: false, reason: 'readonly'}}; "
            f"const t = e.tagName.toLowerCase(); "
            f"const isTypingInput = t !== 'input' || !['hidden','file','submit','reset','button','checkbox','radio']"
            f".includes((e.getAttribute('type')||'').toLowerCase()); "
            f"const isEdt = e.isContentEditable === true || t === 'textarea' || t === 'select' || (t === 'input' && isTypingInput); "
            f"if (!isEdt) return {{ok: false, reason: 'not_editable'}}; "
            f"e.scrollIntoView({{block: 'center'}}); e.focus(); return {{ok: true}}; }})()")
        # 兼容旧桩返回 bool（True=聚焦成功，False=目标缺失）
        if state is True or (isinstance(state, dict) and state.get("ok")):
            self._send("Input.insertText", {"text": text})
            return {"typed": text, "ref": ref, "method": "ref"}
        reason = state.get("reason") if isinstance(state, dict) else "gone"
        if reason in ("disabled", "readonly", "not_editable"):
            raise LookupError(
                f"目标 {ref} 不可输入（{reason}）。请重新 browser_find / browser_snapshot "
                f"选择原生 input、textarea 或真正可编辑的 contenteditable 元素，不要 browser_inspect。")
        raise SoMStaleError(
            f"输入目标 {ref} 已失效（stale_target）。请重新 browser_find / browser_snapshot "
            f"获取最新结构化 ref，不要直接 browser_inspect。")

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

    # =====================================================
    # P2-5 动态页面观察：结构锚 + semantic fingerprint + 状态变化等待 + semantic delta
    # 设计约束（gtp 评审定稿）：
    #   - message-list-* 只是“候选证据”，不是唯一长期锚；
    #   - 禁止“最大可滚动 div”做主 fallback，改成候选评分；
    #   - checksum 不是 hash innerText，而是排除时间/按钮/chips/CSS hash/几何 的 semantic fingerprint；
    #   - 不假设“非 user = assistant”，先算 delta 再剔除噪音得到 assistant_candidate；
    #   - 状态机 WAITING→STARTED→GENERATING→COMPLETED + 异常 ANCHOR_LOST/NO_CHANGE/TIMEOUT/LOADING_STUCK。
    #   - 不做豆包专用工具；不依赖 LLM 猜 selector / SoM / inspect。
    # =====================================================

    _MESSAGE_ROOT_JS = r"""
(() => {
  const main = document.querySelector('main');
  if (!main) return { error: 'no main', candidates: [] };
  const wantUserText = __USER__;
  const prevStem = __STEM__;
  const out = [];
  const seen = new Set();
  const scan = (root) => {
    if (!root || root.nodeType !== 1 || seen.has(root)) return;
    seen.add(root);
    if (root.offsetParent === null) return;
    const r = root.getBoundingClientRect();
    if (r.height < 240 || r.width < 200) return;
    const cls = ((root.className || '') + '').replace(/\s+/g, ' ').slice(0, 90);
    const lc = cls.toLowerCase();
    const scrollable = root.scrollHeight > root.clientHeight + 2 || root.scrollWidth > root.clientWidth + 2;
    let hasEdit = false, text = '';
    for (const sel of ['textarea', 'input', '[contenteditable]']) {
      if (root.querySelector(sel)) { hasEdit = true; break; }
    }
    try { text = (root.innerText || '').trim(); } catch (e) { text = ''; }
    let score = 0;
    if (/message[-_]?list/.test(lc)) score += 130;          // 语义 class 主证据（只是候选信号，非硬编码唯一锚）
    else if (/conversation|session|thread|dialog|chatlog/.test(lc)) score += 50;
    if (scrollable) score += 25;
    if (hasEdit) score -= 60;                               // 含输入框 => 更像 composer
    if (/composer|inputbox|input-area|send-msg-input/.test(lc)) score -= 60;
    if (r.width < 260) score -= 80;                         // 过窄 => 侧栏/工具条
    if (/conversation-item|sidebar|recent|history|chatsider/.test(lc)) score -= 70;   // 侧栏/最近会话
    if (/recommend|suggestion|discover|explore|hot-slot|quick/.test(lc)) score -= 40; // 推荐区
    const stem = (cls.trim().split(/\s+/).find(t => /[a-zA-Z]/.test(t)) || '');
    if (prevStem && stem && stem.toLowerCase().startsWith(prevStem.toLowerCase())) score += 40; // 结构相似加分
    if (wantUserText && text && text.includes(wantUserText)) score += 30;                       // 含已知 user 消息
    // 可信判定：命中"会话区"语义 class 或含已知 user 消息文本 => reliable；
    // 否则仅当在结构上确实像消息区(可滚动+高+文本足+非composer)且得分足够高才放行。
    // 这样"落地态"只有泛大div时 => reliable=false => ANCHOR_LOST，而不是拿最大 div 兜底。
    const strongCls = /message([-_]?list)?|conversation|session|thread|dialog|chatlog|messages/.test(lc);
    const strongText = !!(wantUserText && text && text.includes(wantUserText));
    const structural = scrollable && r.height >= 300 && text.length >= 40 && !hasEdit;
    const reliable = !!(strongCls || strongText || (structural && score >= 100));
    if (score > 0) out.push({ score, cls, stem, scrollable, textLen: text.length, reliable,
                              rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)] });
    if (out.length < 80) {
      for (const k of Array.from(root.children)) {
        if (k.offsetParent !== null && k.getBoundingClientRect().height >= 200) scan(k);
      }
    }
  };
  scan(main);
  const cands = out.sort((a, b) => b.score - a.score).slice(0, 8)
    .map((c, i) => ({ score: c.score, rank: i, cls: c.cls, stem: c.stem, reliable: !!c.reliable,
                      scrollable: c.scrollable, textLen: c.textLen, rect: c.rect }));
  return { candidates: cands };
})()
"""

    _PIN_ROOT_JS = r"""
(() => { try { document.querySelectorAll('[data-miniyu-anchor]').forEach(e => e.removeAttribute('data-miniyu-anchor')); } catch(e) {}
  const stem = __STEM__;
  if (!stem) return false;
  for (const el of Array.from(document.querySelectorAll('*'))) {
    if (el.offsetParent === null) continue;
    const c = ((el.className || '') + '').replace(/\s+/g, ' ');
    if (c === stem || c.split(/\s+/).indexOf(stem) >= 0) {
      el.setAttribute('data-miniyu-anchor', '1'); return true;
    }
  }
  return false; })()
"""

    _SEMANTIC_STATE_JS = r"""
(() => {
  const root = document.querySelector('[data-miniyu-anchor]');
  if (!root || root.offsetParent === null) return { error: 'anchor_lost' };
  const raw = ((root.innerText || '') + '').replace(/\u00a0/g, ' ');
  const lines = raw.split('\n').map(s => s.trim()).filter(Boolean);
  const noise = (l) =>
     /^\d{1,2}[:：]\d{2}/.test(l) ||                                // 11:56 / 12:08
     /^(今天|昨天|明天|前天|晚上|上午|下午|早上)\s*\d{1,2}[:：]\d{2}/.test(l) ||
     /^\d{4}[-/年]\d{1,2}([-/月]\d{1,2})?$/.test(l) ||             // 2026-09-12
     /^\d{1,2}月\d{1,2}日$/.test(l);
  const sem = lines.filter(l => !noise(l));
  const s = sem.join('\n');
  let h = 5381;
  for (let i = 0; i < s.length; i++) { h = ((h << 5) + h + s.charCodeAt(i)) | 0; }
  let loading = false;
  const busySel = '[class*="loading" i], [class*="streaming" i], [class*="generating" i], [aria-busy="true"]';
  for (const el of Array.from(document.querySelectorAll(busySel))) {
    if (el.offsetParent !== null && el.getBoundingClientRect().height > 0 && el.getBoundingClientRect().width > 0) {
      loading = true; break;
    }
  }
  return { fingerprint: (h >>> 0).toString(16), semantic_text: s,
           sem_block_count: sem.length, loading: loading };
})()
"""

    def discover_message_root(self, baseline_user_text=None):
        """Phase B：正式 Observation Anchor —— 在 main 内做候选 conversation root 评分。

        - message-list-* 仅是候选信号，绝不硬编码为唯一长期锚；
        - ok 判定收口在方法内：仅当候选命中断言（reliable）才返回 ok=True 并 pin；
        - 找不到可靠 conversation root => ok=False（上层即 ANCHOR_LOST），
          绝不回退到“最大可滚动 div”掩盖问题；
        - pin 通过 stem（structural signature）重定位元素，SPA 重渲染后可重新 discover。
        """
        if not hasattr(self, "_observation_anchor"):
            self._observation_anchor = None
        self._last_anchor_user = baseline_user_text if baseline_user_text else None
        js = self._MESSAGE_ROOT_JS \
            .replace("__USER__", json.dumps(baseline_user_text if baseline_user_text else None)) \
            .replace("__STEM__", json.dumps(getattr(self, "_message_root_stem", None)))
        try:
            res = self._evaluate(js) or {}
        except Exception as e:
            return {"ok": False, "error": "scan:" + str(e)[:60]}
        cands = res.get("candidates") or []
        if not cands:
            return {"ok": False, "error": res.get("error") or "no candidate"}
        best = cands[0]
        if not best.get("reliable"):
            self._observation_anchor = None
            return {"ok": False, "reliable": False, "best": best, "candidates": cands[:5],
                    "error": "no reliable conversation anchor (best score=%s)" % best.get("score")}
        if not best.get("stem"):
            return {"ok": False, "reliable": True, "best": best, "candidates": cands[:5],
                    "error": "reliable anchor lacks stable stem"}
        self._message_root_stem = best.get("stem")
        pinned = self._evaluate(self._PIN_ROOT_JS.replace(
            "__STEM__", json.dumps(best.get("stem"))))
        if not pinned:
            return {"ok": False, "reliable": True, "best": best, "candidates": cands[:5],
                    "error": "pin_failed"}
        self._observation_anchor = {"stem": best.get("stem"), "score": best.get("score"),
                                    "cls": best.get("cls"), "rect": best.get("rect"), "reliable": True}
        return {"ok": True, "best": best, "candidates": cands[:5], "anchor": dict(self._observation_anchor)}

    def _ensure_anchor(self):
        """SPA 重渲染会抹掉 imperative 的 data-miniyu-anchor，读取前自愈：
        (1) 锚尚在 → True；(2) 有 stem → 按 stem 重新 pin；(3) 仍失败 → 重新 discover。
        全部失败返回 False，上层按 anchor_lost 处理。"""
        try:
            if self._evaluate("!!document.querySelector('[data-miniyu-anchor]')"):
                return True
        except Exception:
            pass
        stem = getattr(self, "_message_root_stem", None)
        if stem:
            try:
                if self._evaluate(self._PIN_ROOT_JS.replace("__STEM__", json.dumps(stem))):
                    return True
            except Exception:
                pass
        try:
            res = self.discover_message_root(getattr(self, "_last_anchor_user", None))
            return bool(res.get("ok"))
        except Exception:
            return False

    def semantic_state(self):
        """Phase B：读取结构锚的 semantic fingerprint（排除时间/按钮/chips/几何），分离 loading。"""
        try:
            if not self._ensure_anchor():
                return {"error": "anchor_lost"}
            return self._evaluate(self._SEMANTIC_STATE_JS) or {"error": "empty"}
        except Exception as e:
            return {"error": "eval:" + str(e)[:60]}

    # ------------------------------------------------------------------
    # P2-5 Phase D：semantic blocks —— 按 DOM 结构区分 assistant 正文(content)
    #                与推荐 chips/控件(control)，非文本黑名单、非具体 class 名黑名单。
    # ------------------------------------------------------------------
    _NOISE_RE = re.compile(
        r'^\d{1,2}[:：]\d{2}$'                                   # 11:56 / 12:08
        r'|^(今天|昨天|明天|前天|晚上|上午|下午|早上)\s*\d{1,2}[:：]\d{2}$'
        r'|^\d{4}[-/年]\d{1,2}([-/月]\d{1,2})?$'                # 2026-09-12
        r'|^\d{1,2}月\d{1,2}日$'
    )
    _BLOCKS_JS = r"""
(() => {
  const root = document.querySelector('[data-miniyu-anchor]') || document.body;
  const vis = (el) => { const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const c = getComputedStyle(el);
    return c.display !== 'none' && c.visibility !== 'hidden' && c.opacity !== '0'; };
  const clsTok = (el) => { let c = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '');
    return ('' + c).split(/\s+/).filter(Boolean); };
  // 结构性“建议/推荐引流区”容器：类名含 suggest/recommend 语义 token（稳定前缀，非 hash）
  const isSuggestTok = (tok) => /^(suggest|recommend|quick)/i.test(tok);
  const suggestContainers = new Set();
  for (const el of Array.from(root.querySelectorAll('*')))
    if (clsTok(el).some(isSuggestTok)) suggestContainers.add(el);
  const inSuggestionArea = (el) => {
    for (let p = el; p && p !== document.body; p = p.parentElement)
      if (suggestContainers.has(p)) return true;
    return false; };
  const leaves = [];
  let idx = 0;
  for (const el of Array.from(root.querySelectorAll('*'))) {
    const it = (el.innerText || ''); const tc = (el.textContent || '');
    if (!it || !vis(el)) continue;
    if (it.trim() !== tc.trim()) continue;                     // 只取直接持有文本的叶子
    const t = it.trim().replace(/\s+/g, ' ').replace(/\u00a0/g, ' ');
    if (!t) continue;
    const role = el.getAttribute ? (el.getAttribute('role') || '') : '';
    const c = getComputedStyle(el); const r = el.getBoundingClientRect();
    const clickable = role === 'button' || role === 'link' || /^(button|radio|checkbox)$/.test(role) ||
                      el.tagName === 'BUTTON' || el.tagName === 'A' || /pointer/.test(c.cursor);
    const suggestion = inSuggestionArea(el);
    const short = t.length <= 40;
    const control = suggestion || (clickable && short);        // 结构判定：引流区 或 可点短控件
    leaves.push({ line: t, control, clickable, suggestion, short,
                  tag: (el.tagName || '').toLowerCase(), role,
                  rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
                  idx: idx++ });
  }
  return leaves;
})()
"""

    def semantic_baseline(self, user_message_text=None):
        """Phase D：以“叶级”文本行构建与 semantic_blocks 一致分节的基线。
        避免整锚 innerText 把同一段正文拆成多行导致历史漏排除。"""
        baseline = {}
        try:
            st = self.semantic_state()
            baseline["fingerprint"] = st.get("fingerprint")
            baseline["semantic_text"] = st.get("semantic_text") or ""
            baseline["sem_block_count"] = st.get("sem_block_count") or 0
        except Exception:
            pass
        leaf_lines = set()
        try:
            for leaf in (self._evaluate(self._BLOCKS_JS) or []):
                n = re.sub(r"\s+", " ", (leaf.get("line") or "")).strip().replace("\u00a0", " ")
                if n and not self._NOISE_RE.match(n):
                    leaf_lines.add(n)
        except Exception:
            pass
        baseline["leaf_lines"] = sorted(leaf_lines)
        if user_message_text:
            baseline["user_message_text"] = user_message_text
        return baseline

    def semantic_blocks(self, baseline=None):
        """Phase D：把 message-root 内的新增文本叶节点结构化为 blocks。

        - 按 anchor 内 DOM 结构分类：control(推荐 chips/控件) vs content(assistant 正文)；
        - 过滤掉 baseline 已存在的行与用户自己刚发的文本 → 只保留“新增”块；
        - 返回 {blocks:[{kind,text,handle,...}], assistant_reply}，
          assistant_reply = 仅 content 块按 DOM 顺序拼接（纯正文，不含 chips）。
        - 判定规则（与 _BLOCKS_JS 输出的结构事实一致，语义不变）：
          control = 位于 suggestion 容器 || (可点击 && 短文本)。
          规则留在 Python 侧便于加回归测试；真实豆包验证通过后保持该规则不变。
        """
        baseline = baseline or {}
        if not self._ensure_anchor():
            return {"blocks": [], "assistant_reply": ""}
        try:
            leaves = self._evaluate(self._BLOCKS_JS) or []
        except Exception:
            leaves = []
        base_lines = set()
        for l in (baseline.get("leaf_lines") or []):            # 首选叶级（分节一致）
            s = re.sub(r"\s+", " ", l).strip().replace("\u00a0", " ")
            if s:
                base_lines.add(s)
        if not base_lines:                                       # 兼容旧 semanc text 基线
            for l in (baseline.get("semantic_text") or "").split("\n"):
                s = re.sub(r"\s+", " ", l).strip().replace("\u00a0", " ")
                if s:
                    base_lines.add(s)
        user = re.sub(r"\s+", " ", baseline.get("user_message_text") or "").strip()
        leaves.sort(key=lambda x: x.get("idx", 0))
        blocks = []
        seen = set()
        for leaf in leaves:
            line = leaf.get("line") or ""
            key = line + "|" + str(leaf.get("rect"))
            if key in seen:
                continue
            seen.add(key)
            normalized = re.sub(r"\s+", " ", line)
            if normalized in base_lines or (user and normalized == user):
                continue                                   # 既有历史 / 自己刚发的行
            if self._NOISE_RE.match(normalized):
                continue                                   # 时间等噪声行（与 fingerprint 过滤一致）
            is_control = bool(leaf.get("suggestion")) or (bool(leaf.get("clickable")) and bool(leaf.get("short")))
            kind = "control" if is_control else "content"
            blocks.append({"kind": kind, "text": line,
                           "handle": "b%d" % leaf.get("idx", 0),
                           "tag": leaf.get("tag") or "", "role": leaf.get("role") or "",
                           "control": is_control,
                           "suggestion": bool(leaf.get("suggestion"))})
        assistant_reply = "\n".join(b["text"] for b in blocks if b["kind"] == "content")
        return {"blocks": blocks, "assistant_reply": assistant_reply}

    def semantic_delta(self, baseline=None, current=None):
        """Phase D：纯 assistant 正文（content 块），不含推荐 chips/控件。"""
        return self.semantic_blocks(baseline=baseline)["assistant_reply"]

    # ------------------------------------------------------------------
    # P2-6 Agent Integration Layer：把已验证的 P2-5 能力透成 Agent 可调用、
    # LLM 无需构造内部状态的语义接口。
    # ------------------------------------------------------------------
    def capture_wait_baseline(self, user_text=None):
        """browser_type(..., press_enter=True) 发送成功后调用：把当前语义状态存为
        pending baseline，供后续 wait_for_changes() 不带参数时消费。
        user_text=发送文本，用于在 delta 里滤掉用户刚发的消息（即使捕获前它尚未渲染进 DOM）。

        pending 属于 controller 内部状态，不暴露给 LLM。
        """
        base = self.semantic_baseline(user_message_text=user_text)
        self._pending_wait_baseline = base
        return {"captured": True, "pending": base is not None,
                "sem_block_count": base.get("sem_block_count"),
                "leaf_count": len(base.get("leaf_lines") or [])}

    def read_latest_reply(self):
        """通用语义读取：读取当前页面最新一条 assistant/response 正文（非豆包专用）。

        内部按顺序复用：discover/_ensure_anchor → semantic_blocks → control 过滤 → dedup → read。
        不把 message-list-*/CSS hash/semantic_blocks/DOM 结构暴露给 LLM。

        返回：
          {"success": True, "text": <正文>, "source": "structured_read", "pending": bool}
          失败状态：ANCHOR_LOST / NO_PENDING_WAIT_BASELINE / EMPTY_REPLY。
        """
        if not self._ensure_anchor():
            return {"success": False, "state": "ANCHOR_LOST"}
        pending = getattr(self, "_pending_wait_baseline", None)
        baseline = pending if pending is not None else {}
        blocks = self.semantic_blocks(baseline=baseline).get("blocks", [])
        contents = [b for b in blocks if b.get("kind") == "content"]
        if not contents:
            return {"success": False, "state": "NO_PENDING_WAIT_BASELINE" if pending is None
                    else "EMPTY_REPLY", "anchor": True, "pending": pending is not None}
        text = "\n".join(b["text"] for b in contents)
        return {"success": True, "text": text, "source": "structured_read",
                "pending": pending is not None, "block_count": len(contents)}

    def wait_for_changes(self, baseline=None, timeout=60.0, min_stable_rounds=2, interval=1.0, trace=None):
        """Phase C：状态机等待“回复完成”。

        文案与返回全部结构化，状态机：
          WAITING → STARTED → GENERATING → COMPLETED
          异常：ANCHOR_LOST / NO_CHANGE / TIMEOUT / LOADING_STUCK
        完成 = delta_detected + loading 消失 + semantic fingerprint 稳定 min_stable_rounds 轮。
        trace：可选回调，每轮轮询调用 trace({"tick", "state", "loading", "delta", "stable"})，
               仅用于演示/调试，不影响判定。

        P2-6：baseline 可省略 —— 若未提供（dict/None 均视为空），优先使用 chat 发送时
        browser_type(press_enter) 自动捕获的 pending baseline；连 pending 都没有则返回
        NO_PENDING_WAIT_BASELINE，绝不要求 LLM 构造 semantic baseline。
        """
        baseline = baseline or {}

        # P2-6：空 baseline → 用发送时自动捕获的 pending；无 pending → 明确报错
        if not baseline:
            pending = getattr(self, "_pending_wait_baseline", None)
            if pending is None:
                return {"success": False, "state": "NO_PENDING_WAIT_BASELINE"}
            baseline = dict(pending)

        try:
            timeout = float(timeout); min_stable_rounds = int(min_stable_rounds); interval = float(interval)
        except Exception:
            return {"success": False, "state": "TIMEOUT", "error": "param"}

        # 确保锚存在（SPA 重渲染后结构重定位；真正的执行态取一票，error 态不算）
        if not self._ensure_anchor():
            return {"success": False, "state": "ANCHOR_LOST"}

        base_fp = baseline.get("fingerprint") or (self.semantic_state() or {}).get("fingerprint")
        t0 = time.monotonic()
        started = False
        state = "WAITING"
        loading = False
        last_fp = None
        stable = 0
        tick = 0

        while True:
            st = self.semantic_state()
            if st.get("error") == "anchor_lost":
                return {"success": False, "state": "ANCHOR_LOST",
                        "started": started, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
            fp = st.get("fingerprint")
            loading = bool(st.get("loading"))
            delta = bool(fp) and fp != base_fp
            if delta and not started:
                started = True
                state = "STARTED"
            if started:
                if loading:
                    state = "GENERATING"
                    stable = 0
                else:
                    if fp is not None and fp == last_fp:
                        stable += 1
                    else:
                        stable = 0
                    if stable >= min_stable_rounds:
                        blocks = self.semantic_blocks(baseline)
                        return {"success": True, "state": "COMPLETED", "started": started,
                                "loading": False, "delta_detected": True,
                                "elapsed_ms": int((time.monotonic() - t0) * 1000),
                                "message_delta": blocks["assistant_reply"],
                                "message_blocks": blocks["blocks"],
                                "fingerprint": fp}
            last_fp = fp
            tick += 1
            if trace is not None:
                try:
                    trace({"tick": tick, "state": state, "loading": loading,
                           "delta": bool(delta), "stable": stable})
                except Exception:
                    pass
            if time.monotonic() - t0 > timeout:
                if not started:
                    return {"success": False, "state": "NO_CHANGE", "started": False,
                            "loading": loading, "delta_detected": False,
                            "elapsed_ms": int((time.monotonic() - t0) * 1000),
                            "last_fingerprint": last_fp}
                return {"success": False, "state": "LOADING_STUCK" if loading else "TIMEOUT",
                        "started": started, "loading": loading, "delta_detected": True,
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                        "last_fingerprint": last_fp}
            time.sleep(interval)

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
            "elements": self._alloc_handles([t.to_dict() for t in targets]),
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