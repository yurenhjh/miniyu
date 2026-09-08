"""
probe_bailian_code_interpreter.py — 百炼服务端代码解释器探针（真实 API 实测，不计入 pytest）

背景：
  config.yaml 的 web_search.code_interpreter 开关启用后，客户端在"纯计算/纯对话"
  流式回合注入顶层参数 enable_code_interpreter=true（配 enable_thinking），由阿里云
  服务端在云端沙箱里写并运行 Python，解决大数运算/数据分析等复杂问题。

实测结论（本脚本 + 百炼官方文档）：
  - Chat Completions 模式下 code_interpreter 是【顶层布尔参数】，不是 tools 数组条目
    （tools 里放 {"type": "code_interpreter"} 是 Responses API 写法，本端点 400
    "'function' is a required property"）
  - 仅支持流式调用（非流式 400 "Non-streaming mode does not support Code interpreter"）
  - 不能与本地函数工具同请求（400 "Agent mode does not support tools"）
  - qwen3.7-flash / qwen3.5-plus 均支持，能算出精确结果（123²¹ = 77,269,364,466,549,865,653,073,473,388,030,061,522,211,723）

场景：
  A. 原始 API：enable_code_interpreter + enable_thinking + stream —— 当前模型能否真算出结果
  B. 项目真实客户端路径：create_llm_client → chat_stream(tools=None) 计算回合
     —— 请求体是否正确注入 enable_code_interpreter、回复是否含精确计算结果
  C. 注入门控：非流式 chat() 与"带本地工具"的流式回合均不应注入 enable_code_interpreter
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.agent_config import load_config
from core.llm_client import create_llm_client

QUERY = "请用代码解释器计算 123 的 21 次方是多少？"
EXPECTED_DIGITS = "77,269,364,466,549,865,653,073,473,388,030,061,522,211,723"


def part_a_raw_api(cfg):
    """场景 A：原始 API 调用，验证当前模型支持 code_interpreter 并真能计算"""
    print("\n" + "=" * 22 + " A. 原始 API：enable_code_interpreter + stream " + "=" * 22)
    body = {
        "model": cfg["llm"]["model"],
        "messages": [{"role": "user", "content": QUERY}],
        "enable_code_interpreter": True,
        "enable_thinking": True,
        "stream": True,
    }
    try:
        r = requests.post(
            f"{cfg['llm']['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['llm']['api_key']}"},
            json=body, timeout=180, stream=True,
        )
    except Exception as e:
        print(f"[网络异常] {e}")
        return
    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"错误详情: {r.text[:500]}")
        return
    contents, reasonings = [], []
    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if raw == "data: [DONE]":
            break
        if raw.startswith("data: "):
            try:
                chunk = json.loads(raw[6:])
            except Exception:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta", {}) or {}
            if delta.get("content"):
                contents.append(delta["content"])
            if delta.get("reasoning_content"):
                reasonings.append(delta["reasoning_content"])
    r.close()
    text = "".join(contents)
    print(f"reasoning_content 出现: {bool(reasonings)}")
    print(f"回复内容: {text[:300]}")
    hit = EXPECTED_DIGITS.replace(",", "") in text.replace(",", "")
    print(f"[结论A] 算出精确结果 123^21: {'是 ✅' if hit else '否 ❌'}")


def part_b_client_path(cfg):
    """场景 B：走项目真实客户端路径（纯计算回合 tools=None → 注入解释器）"""
    print("\n" + "=" * 22 + " B. 项目客户端路径（chat_stream + tools=None） " + "=" * 22)
    client = create_llm_client(cfg)
    raw = getattr(client, "primary", client)
    print(f"is_bailian: {raw.is_bailian}")
    print(f"server_code_interpreter_active: {raw.server_code_interpreter_active}")

    text = ""
    for chunk in client.chat_stream([{"role": "user", "content": QUERY}], tools=None):
        if chunk.finish_reason == "streaming":
            text += chunk.text
    print(f"回复内容: {text[:300]}")
    hit = EXPECTED_DIGITS.replace(",", "") in text.replace(",", "")
    print(f"server_code_interpreter_active（请求后）: {raw.server_code_interpreter_active}")
    print(f"[结论B] 纯计算回合注入解释器并算出精确结果: {'是 ✅' if hit else '否 ❌'}")


def part_c_gating(cfg):
    """场景 C：注入门控——非流式与带本地工具的回合不应注入 enable_code_interpreter"""
    print("\n" + "=" * 22 + " C. 注入门控验证 " + "=" * 22)
    client = create_llm_client(cfg)
    raw = getattr(client, "primary", client)

    body = {"model": raw.model, "messages": [{"role": "user", "content": "hi"}]}
    raw._apply_server_tools(body, tools=None, stream=False)   # 非流式 + 无工具
    print(f"非流式+无工具: enable_code_interpreter={body.get('enable_code_interpreter')!r} "
          f"(应为 None/False) {'✅' if not body.get('enable_code_interpreter') else '❌'}")

    func_tool = {"type": "function", "function": {"name": "run_command",
                 "parameters": {"type": "object", "properties": {}}}}
    body2 = {"model": raw.model, "messages": [{"role": "user", "content": "hi"}],
             "tools": [func_tool]}
    raw._apply_server_tools(body2, tools=[func_tool], stream=True)  # 流式 + 本地工具
    print(f"流式+本地工具: enable_code_interpreter={body2.get('enable_code_interpreter')!r} "
          f"(应为 None/False) {'✅' if not body2.get('enable_code_interpreter') else '❌'}")

    body3 = {"model": raw.model, "messages": [{"role": "user", "content": "hi"}]}
    raw._apply_server_tools(body3, tools=None, stream=True)   # 流式 + 无工具（纯计算回合）
    print(f"流式+无工具: enable_code_interpreter={body3.get('enable_code_interpreter')!r} "
          f"enable_thinking={body3.get('enable_thinking')!r} "
          f"{'✅' if body3.get('enable_code_interpreter') else '❌'}")


if __name__ == "__main__":
    cfg = load_config()
    print(f"模型: {cfg['llm']['model']}  base_url: {cfg['llm']['base_url']}")
    part_a_raw_api(cfg)
    part_b_client_path(cfg)
    part_c_gating(cfg)
    print("\n" + "=" * 60)
    print("探针结束：A/B/C 三项全 ✅ 即代表 code_interpreter 已正确接入并可用。")
