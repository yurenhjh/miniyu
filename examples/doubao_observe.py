# -*- coding: utf-8 -*-
"""P2-5 DOM/AX 侦察：在真实豆包页面找出"会话容器 / 消息节点 / assistant message / loading 状态"。

原则：不看截图、不猜测 selector，全部以运行时真实 DOM / ARIA 结构为准。
流程：
  1. 用持久登录档案（config.yaml browser.user_data_dir）启动浏览器
  2. 打开 https://www.doubao.com/chat
  3. （可选）找到输入框并发送一句"你好"，触发真实 assistant message
  4. 发送后立刻采集一次"loading / 生成中"状态
  5. 轮询直到出现新回复
  6. dump 会话/消息相关 DOM 候选 + 每条消息的角色/属性/文本 + 对齐方向
  7. 用"按 data-miniyu-ref 定向读取"验证单条消息可被精确读回
  8. 记录浏览器内置 find(role=article/listitem) 能否命中消息（测现有体系覆盖度）

结果写 outputs/doubao_dom_report_*.json，并打印精简文本摘要。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser_controller import BrowserController


def _load_browser_cfg():
    try:
        from core.agent_config import load_config
        return load_config().get("browser", {}) or {}
    except Exception:
        return {}


# 发送后立即采集：正在生成 / loading 指示器
_LOADING_JS = r"""
(() => {
  const busy = [];
  const sel = '[class*="loading" i], [class*="thinking" i], [class*="generat" i], '
             + '[class*="typing" i], [class*="streaming" i], [class*="sending" i], '
             + '[class*="generating" i], [aria-busy="true"]';
  document.querySelectorAll(sel).forEach((el) => {
    if (el.offsetParent === null) return;
    const r = el.getBoundingClientRect();
    busy.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || '').toString().slice(0, 60),
      role: el.getAttribute('role') || '',
      txt: (el.innerText || '').trim().slice(0, 30),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  });
  return busy;
})()
"""

# 会话/消息相关候选节点 + 每条的消息角色/文本/对齐方向
_MESSAGE_JS = r"""
(() => {
  const seen = new Set();
  const out = [];
  const add = (el, why) => {
    if (seen.has(el)) return; seen.add(el);
    const r = el.getBoundingClientRect();
    const attrs = {};
    for (let i = 0; i < el.attributes.length; i++) {
      const a = el.attributes[i];
      if (/^(data-|aria-|role|id)/i.test(a.name)) attrs[a.name] = a.value.slice(0, 40);
    }
    // 对齐方向：距离最近可滚动内容盒的左/右沿，粗略判别 user(右)/assistant(左)
    let align = '';
    let box = el.closest('[data-scrollbox],[class*="scroll" i]') || document.body;
    const br = box.getBoundingClientRect();
    if (r.width && br.width) {
      const distL = Math.abs(r.left - br.left), distR = Math.abs((br.left + br.width) - (r.left + r.width));
      if (distL < 40) align = 'left';
      else if (distR < 40) align = 'right';
    }
    out.push({
      why: why,
      tag: el.tagName.toLowerCase(),
      path: (() => { const p = []; let n = el;
        while (n && n.nodeType === 1 && p.length < 8) { p.unshift(n.tagName.toLowerCase()); n = n.parentElement; }
        return p.join('/'); })(),
      role: el.getAttribute('role') || '',
      id: el.id || '',
      title: ((el.className || '') + ' ' + (el.getAttribute('data-testid') || '')).trim().slice(0, 90),
      attrs: attrs,
      align: align,
      visible: el.offsetParent !== null,
      text: (el.innerText || el.textContent || '').trim().slice(0, 80),
      sub_msgs: el.querySelectorAll ? (el.matches && el.querySelectorAll('article,[role=article]').length) : 0,
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
    });
  };
  // 候选选择器：显式语义角色 + 常见 class/data-testid 关键词
  const sels = [
    '[role="article"]', 'article', '[role="listitem"]', '[role="feed"]', '[role="status"]',
    '[role="main"]', '[role="region"]', '[role="log"]',
    '[class*="message" i]', '[class*="conversation" i]', '[class*="chat-item" i]',
    '[class*="bubble" i]', '[data-testid*="message" i]', '[data-testid*="chat" i]',
  ];
  for (const s of sels) document.querySelectorAll(s).forEach((e) => add(e, s));
  return out.length > 60 ? out.slice(0, 60) : out;
})()
"""

# 会话内容取证 v2：定位 main 内 message-list 容器，dump"真正承文节点"序列（排除纯包裹层/时间戳/用户气泡）
# 每个承文节点贴 data-miniyu-ref 供定向读回；assistant 读回上溯到该消息行（direct child of box），拿整条回复。
_CONV_TEXT_JS = r"""
(() => {
  const main = document.querySelector('main');
  if (!main) return { error: 'no main' };
  let box = null;
  for (const el of main.querySelectorAll('[class*="message-list" i], [class*="message_list" i]')) {
    if (el.offsetParent !== null) { box = el; break; }
  }
  if (!box) {
    let best = null, bestArea = -1;
    for (const el of main.querySelectorAll('div')) {
      if (el.offsetParent === null) continue;
      const r = el.getBoundingClientRect();
      const a = r.width * r.height;
      if (a > bestArea) { bestArea = a; best = el; }
    }
    box = best;
  }
  if (!box) return { error: 'no conversation box' };
  const br = box.getBoundingClientRect();
  const name = (el) => String(el.className || '').replace(/\s+/g, ' ').slice(0, 60);
  const alignOf = (el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !br.width) return '';
    const dl = Math.abs(r.left - br.left), dr = Math.abs((br.left + br.width) - (r.left + r.width));
    if (dl < 40) return 'left';
    if (dr < 40) return 'right';
    return '';
  };
  const isTime = (t) => /^\s*(\d{1,2}[:：]\d{1,2})\s*$/.test(t.trim())
    || /^\d{4}[-/年]/.test(t.trim());
  const nodes = [];
  for (const el of Array.from(box.querySelectorAll('*'))) {
    if (el.offsetParent === null) continue;
    const it = (el.innerText || '').trim();
    if (!it || it.length < 1) continue;
    // 跳过"子节点承载了完全相同文本"的纯包裹层 -> 只留真正承文节点
    let tight = true;
    for (const c of el.children) {
      if (c && c.offsetParent !== null && (c.innerText || '').trim() === it) { tight = false; break; }
    }
    if (!tight) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 2) continue;
    if (isTime(it)) continue;
    const cls = name(el);
    const isUser = cls.toLowerCase().indexOf('send-msg') >= 0;
    const idx = nodes.length + 1;
    const ref = 'eobs' + idx;
    el.setAttribute('data-miniyu-ref', ref);
    nodes.push({ ref, tag: el.tagName.toLowerCase(), cls, align: alignOf(el),
                 isUser, text: it.slice(-200),
                 rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)] });
    if (nodes.length >= 40) break;
  }
  // latest_assistant：最后一个非用户、非时间戳的承文节点；读回时上溯到其在 box 下的直接子消息行
  let latest_assistant = null;
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (n.isUser) continue;
    if (isTime(n.text)) continue;
    latest_assistant = n; break;
  }
  let latest_assistant_ref = null, latest_assistant_text = null;
  if (latest_assistant) {
    let host = document.querySelector('[data-miniyu-ref="' + latest_assistant.ref + '"]');
    if (host) {
      let row = host;
      while (row.parentElement && row.parentElement !== box) row = row.parentElement;
      if (row.parentElement !== box) row = host;
      row.setAttribute('data-miniyu-ref', 'eass1');
      latest_assistant_ref = 'eass1';
      latest_assistant_text = (row.innerText || '').trim().slice(-300);
    }
  }
  return {
    container: { tag: box.tagName.toLowerCase(), cls: name(box),
                 rect: [Math.round(br.left), Math.round(br.top), Math.round(br.width), Math.round(br.height)] },
    item_count: nodes.length,
    items: nodes,
    latest_assistant_ref: latest_assistant_ref,
    latest_assistant_text: latest_assistant_text,
    tail_text: (box.innerText || '').trim().slice(-400),
  };
})()
"""


def _text_of(bc, selector):
    try:
        return (bc._evaluate(
            f"(() => {{ const e = document.querySelector({json.dumps(selector)}); "
            f"return e ? (e.innerText||e.textContent||e.value||'') : null; }})()") or "")
    except Exception:
        return ""


def _new_items(conv, base_texts=None):
    """从一次 _CONV_TEXT_JS 结果里挑出"既不在发送前基线、又不是时间戳"的新承文节点。
    语义信号的唯一来源 = 消息里真正多出来的文本；不依赖猜测 selector。"""
    base_texts = base_texts or set()
    out = []
    for it in (conv.get("items") or []):
        t = (it.get("text") or "").strip()
        if not t or t in base_texts:
            continue
        if re.match(r"^(今天|昨天|明天)?\s*\d{1,2}[:：]\d{1,2}$", t):
            continue
        if re.match(r"^\d{4}[-/年]", t):
            continue
        out.append(it)
    return out


def main():
    cfg = _load_browser_cfg()
    profile = os.path.expandvars(cfg.get("user_data_dir") or "")
    headless = cfg.get("headless", False)
    executable = cfg.get("executable") or None
    send_hello = "--no-send" not in sys.argv

    bc = BrowserController(ws_url="placeholder")
    bc.launch(port=9222, headless=headless, chrome_path=executable,
              user_data_dir=profile or None)
    print("[1] 浏览器已启动（headless=%s，档案=%r）" % (headless, profile))
    bc.navigate("https://www.doubao.com/chat")

    # 等页面骨架出现
    for _ in range(40):
        try:
            if (bc._evaluate("document.body ? document.body.innerText.length : 0") or 0) > 50:
                break
        except Exception:
            pass
        time.sleep(0.5)
    print("[2] 页面已加载，检测是否有输入框…")

    report = {"url": "https://www.doubao.com/chat", "sent_hello": False,
              "loading_after_send": [], "messages": [], "send_result": None,
              "find_coverage": {}, "targeted_read": {}}

    # 找输入框（现有 find 链路）
    textbox = None
    try:
        items = bc.find(role="textbox")
        if items:
            textbox = items[0]
    except Exception:
        items = []

    # 发送前先取 message-list 现有消息项文本集合，排除"新对话"首页推荐噪音，保证信号=真正的 assistant 新增
    base_conv = bc._evaluate(_CONV_TEXT_JS) or {}
    base_texts = set()
    for b in (base_conv.get("items") or []):
        t = (b.get("text") or "").strip()
        if t:
            base_texts.add(t)

    def _is_noise(text):
        t = (text or "").strip()
        if not t or len(t) < 2:
            return True
        if re.match(r"^(今天|昨天|明天)?\s*\d{1,2}[:：]\d{1,2}$", t):   # 时间戳
            return True
        if re.match(r"^\d{4}[-/年]", t):                                # 日期
            return True
        return False

    if send_hello:
        print("[3] 发送『你好』（失败自动重新 find，最多 3 次）…")
        ok = False
        for attempt in range(3):
            try:
                items = bc.find(role="textbox")
                if not items:
                    print("  第%d次：未找到 textbox" % (attempt + 1))
                    time.sleep(0.5)
                    continue
                tb = items[0]
                bc.type_text("你好", target=tb["handle"])
                bc.press_enter()
                report["send_result"] = {
                    "handle": tb.get("handle"), "typed": "你好",
                    "enter": True,
                }
                report["sent_hello"] = True
                ok = True
                break
            except Exception as e:
                report["send_result"] = {"error": str(e)}
                print("  第%d次失败：%s → 重新 find" % (attempt + 1, str(e)[:70]))
                time.sleep(0.6)
        if ok:
            print("  已发送，等待 assistant 回复出现…")
            time.sleep(0.6)
            report["loading_after_send"] = bc._evaluate(_LOADING_JS) or []
    else:
        print("[3] --no-send，仅做静态侦察")
        time.sleep(0.6)
        report["loading_after_send"] = bc._evaluate(_LOADING_JS) or []

    # 等待真正 assistant 回复：message-list 出现"既不在发送前、又不是我方『你好』/时间戳"的新文本块
    print("[4] 等待回复/消息出现…")
    msgs = bc._evaluate(_MESSAGE_JS) or []
    assistant_seen = []

    def _userish(it):
        # 剔除"自己刚发的 user 消息"：用户气泡类含 send-msg，或文本是"你好+时间戳"
        cls = (it.get("cls") or "").lower()
        if "send-msg" in cls:
            return True
        t = it.get("text") or ""
        if t.startswith("你好") and re.search(r"\d{1,2}[:：]\d{1,2}", t):
            return True
        return False

    stable = 0
    for _ in range(80):
        time.sleep(1.0)
        try:
            msgs = bc._evaluate(_MESSAGE_JS) or []
            conv = bc._evaluate(_CONV_TEXT_JS) or {}
        except Exception:
            conv = {}
        if not report["sent_hello"] and msgs:
            break
        candidates = [x for x in _new_items(conv, base_texts) if not _userish(x)
                      and not (x.get("text") or "").strip().startswith("你好\n")]
        if candidates:
            stable += 1
        else:
            stable = 0
        if stable >= 3:                 # assistant 型新项连续 3 秒稳定
            assistant_seen = candidates
            time.sleep(0.8)             # 让流式收尾一点再 dump
            break
    report["assistant_new_blocks"] = assistant_seen[:20]
    report["messages"] = bc._evaluate(_MESSAGE_JS) or msgs
    msgs = report["messages"]
    print("[5] 采集到消息候选节点 %d 个；检测到 assistant 新消息项 %d 个" % (len(msgs), len(assistant_seen)))

    # 现有 find 体系能否命中消息（评估覆盖度）
    for role in ("article", "listitem", "main", "region"):
        try:
            n = len(bc.find(role=role))
            report["find_coverage"]["role=" + role] = n
        except Exception as e:
            report["find_coverage"]["role=" + role] = "err:" + str(e)[:40]
    try:
        report["find_coverage"]["tag=li"] = len(bc.find(tag="li"))
    except Exception:
        report["find_coverage"]["tag=li"] = 0

    # 会话内容取证：消息项序列 + 最新 assistant 项贴 ref，并用 read_text(selector) 定向读回
    conv_report = bc._evaluate(_CONV_TEXT_JS) or {}
    report["conversation"] = conv_report
    targeted = report["targeted_read"]
    ref = conv_report.get("latest_assistant_ref")
    if ref:
        try:
            got = bc.read_text(selector='[data-miniyu-ref="%s"]' % ref)
            targeted["ref"] = ref
            targeted["read_back"] = (got or "")[:300]
        except Exception as e:
            targeted["error"] = str(e)
    else:
        targeted["error"] = "未识别到 latest assistant 项"

    # 落盘（相对脚本自身定位，避免依赖 ~ 展开到别的目录）
    out_dir = Path(__file__).resolve().parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / ("doubao_dom_report_%d.json" % int(time.time() * 1000))
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[DONE] 报告已写入：", out_file)

    # 精简打印
    print("\n===== 摘要 =====")
    print("发送：", report["send_result"])
    print("loading_after_send：", json.dumps(report["loading_after_send"], ensure_ascii=False)[:300])
    print("find 命中数：", report["find_coverage"])
    print("targeted_read：", json.dumps(report["targeted_read"], ensure_ascii=False)[:200])
    conv = report["conversation"]
    if conv.get("error"):
        print("conversation：", conv)
    else:
        print("conversation容器：", json.dumps(conv.get("container"), ensure_ascii=False))
        print("消息项数：", conv.get("item_count"))
        for b in (conv.get("items") or [])[:40]:
            print("  %-10s align=%-5s text=%s" % ("[" + b["tag"] + "]", b["align"], b["text"][:50]))
        print("latest_assistant_text：", (conv.get("latest_assistant_text") or "")[:120])
        print("latest_assistant_ref：", conv.get("latest_assistant_ref"))
        print("末段文本：…", (conv.get("tail_text") or "")[-200:])
    print("前 8 个消息候选：")
    for m in msgs[:8]:
        print("  -", m["why"], "|", m["tag"], "| role=" + m["role"], "| align=" + m["align"],
              "| title=" + m["title"], "| text=" + m["text"])

    try:
        bc.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()