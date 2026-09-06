"""
web_search.py — HTTP 联网搜索（纯标准库，不开浏览器）

为什么有这一层（背景）
    真机反馈：让 miniyu 联网搜一个"昨天 CSGO 赛事结果"，它思考约 3 分钟仍返回"搜索超时"。
    定位后根因不在模型，而在旧 browser_search 的实现：
      ① 旧实现 = 无头浏览器"打开引擎页 → 快照定位输入框 → type_text → 合成回车"。
         但 CDP 合成回车在必应/百度都不触发表单提交——实测词已打进输入框、按回车后 URL
         仍停在首页，于是内部 wait_for(结果文本) 每次必 10s 超时，模型重试数次就堆到 ~3 分钟；
      ② 百度对自动化另有"安全验证"人机墙，直接请求结果页也会被拦。
    解法 = 绕开浏览器，与云端工具（如 Claude Code 的 WebSearch）同构：HTTP GET 结果页 +
    解析出 {标题, 链接, 摘要}。必应(cn) 实测单次 ~0.9s、b_algo 结果块可稳定解析。

    本模块刻意保持最小、纯标准库（urllib / gzip / html / re），零新增依赖、可离网单测
    （喂一段样本 HTML 验证解析，不发真实请求）。

用法
    from core import web_search
    res = web_search.search("昨天的比赛结果", top=6)   # res["results"] = [{title,url,snippet}, ...]
    失败抛 web_search.WebSearchError（中文原因），由调用方转成模型可见的错误。
"""

import gzip
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

__all__ = ["WebSearchError", "search", "parse_bing_html", "bing_url"]

# 单次搜索默认超时（秒）。必应实测多数 ~1s，最慢见过 7s；留足余量防慢网。
TIMEOUT = 12
# 结果摘要单条上限（字符），避免 text_snippet 过长挤爆模型上下文。
SNIPPET_CAP = 200
# CN 网络下 bing 偶发"远程主机重置连接"（WinError 10054），做 3 次连接级重试兜底。
RETRY_ATTEMPTS = 3
RETRY_DELAY = 0.5

# 伪装桌面版 Edge 的 UA，降低被当爬虫直接拒的概率。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
)


class WebSearchError(Exception):
    """联网搜索失败：网络错误 / 空结果 / 引擎不支持。"""


def bing_url(query):
    """由关键词拼出必应搜索结果页 URL。

    直连 cn.bing.com 而非 www.bing.com：后者会 302 跳转一次，CN 网络下该跳转的二次
    TLS 握手实测偶发被对端重置（WinError 10054）；cn 直连无跳转，3/3 稳定。
    """
    return "https://cn.bing.com/search?q=" + urllib.parse.quote(query)


def _fetch_once(url, timeout=TIMEOUT):
    """单次 GET 页面并解码，返回 (final_url, html_text)。

    只抛原始网络异常（由 _fetch 统一重试/包装）；解压失败这类非瞬时错误直接 WebSearchError。
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            # 只声明 gzip/deflate：urllib 不会自己解 brotli，声明了反而收到解不开的乱码
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            final_url = r.geturl()
            encoding = (r.headers.get("Content-Encoding") or "").lower()
    except urllib.error.HTTPError as e:
        # 4xx/5xx 状态码不是抖动，直接报错（403≈被反爬拦、404≈页面不存在）
        raise WebSearchError(f"HTTP {e.code}: {url}") from e
    except Exception as e:
        # URLError(含重置/超时包装) / socket.timeout / ConnectionResetError 等，交 _fetch 重试
        raise

    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError as e:
            raise WebSearchError(f"响应解压失败(gzip): {e}") from e
    elif encoding == "deflate":
        # 有的服务器发的其实是裸 deflate 流，两种都试
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return final_url, raw.decode("utf-8", errors="replace")


def _fetch(url, timeout=TIMEOUT):
    """带重试的 GET：连接级瞬时错误重试 RETRY_ATTEMPTS 次，仍失败才转 WebSearchError。"""
    last = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return _fetch_once(url, timeout=timeout)
        except WebSearchError:
            raise  # 解压失败 / HTTP 状态码：非瞬时，直接抛
        except Exception as e:  # 连接级抖动：重试
            last = e
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY)
    raise WebSearchError(
        f"网络请求失败(已重试{RETRY_ATTEMPTS}次): {type(last).__name__}: {last}"
    ) from last


_TAG_RE = re.compile(r"<[^>]+>")


def _clean(frag):
    """去掉 HTML 标签、反转义实体、折叠空白。

    Python str 模式下的 \\s 按 Unicode 空白判断（str.isspace()），已涵盖 &nbsp;(U+00A0)、
    U+2000–U+200A(&ensp;/&emsp;…) 等反爬页常出现的不可见空白，统一折叠成一个空格即可。
    """
    s = _TAG_RE.sub("", frag)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# 必应结果块：<li class="b_algo">…</li>
_BING_BLOCK = re.compile(r'<li class="b_algo".*?</li>', re.S)
# 标题行：<h2><a href="…">标题</a></h2>
_TITLE_A = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
# 兜底：块内第一个绝对 <a href>（个别版式标题不在 h2 里）
_ANY_A = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def parse_bing_html(page, top=6):
    """解析必应结果页 HTML → [{title,url,snippet}, ...]，最多 top 条。

    只认 b_algo 结构块；抓不到任何绝对 http(s) 链接的块直接跳过。离网可测：喂一段
    手写样本 HTML 即可验证（见 tests/test_web_http.py）。
    """
    out = []
    for block in _BING_BLOCK.findall(page):
        m = _TITLE_A.search(block) or _ANY_A.search(block)
        if not m:
            continue
        href, title_frag = m.group(1), m.group(2)
        if not href.lower().startswith(("http://", "https://")):
            continue  # 只收绝对链接，跳过 /search? 这类站内相对链接
        title = _clean(title_frag)
        if not title:
            continue
        pm = _SNIPPET_P.search(block)
        snippet = _clean(pm.group(1)) if pm else ""
        out.append({"title": title, "url": html.unescape(href), "snippet": snippet})
        if len(out) >= top:
            break
    return out


def search(query, engine="bing", top=6, timeout=TIMEOUT):
    """联网搜索并返回统一结构；失败抛 WebSearchError。

    返回 {"query", "engine", "url"(最终跳转后), "results"(解析出的原始列表),
          "text_snippet"(给模型看的人读文本，含序号/链接/摘要截断)}
    """
    if not query or not query.strip():
        raise WebSearchError("搜索关键词不能为空")
    if engine != "bing":
        raise WebSearchError(
            f"不支持的搜索引擎: {engine!r}。目前仅接入必应 bing——百度对自动化有"
            "'安全验证'人机墙，直接请求结果页会被拦截，未接入。"
        )
    url = bing_url(query)
    final_url, page = _fetch(url, timeout=timeout)
    results = parse_bing_html(page, top=top)
    if not results:
        raise WebSearchError("没有解析到任何搜索结果（页面可能被反爬拦截或版式改版）。")

    lines = []
    for i, it in enumerate(results, 1):
        snip = it["snippet"]
        if len(snip) > SNIPPET_CAP:
            snip = snip[:SNIPPET_CAP].rstrip() + "…"
        lines.append(f"{i}. {it['title']}\n   {it['url']}\n   {snip}")
    return {
        "query": query,
        "engine": "bing",
        "url": final_url,
        "results": results,
        "text_snippet": "\n".join(lines),
    }
