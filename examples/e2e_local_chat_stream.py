#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
e2e_local_chat_stream.py — 本地模型纯对话模式 + Web SSE 真机验证

复现用户原始痛点：主 API 不可用 → 切本地 Ollama → 让它写故事。
原问题：非流式调用下 7B 模型写 800 token 要 132s，超过 120s 总超时直接失败；
且带 63 个工具 schema 时纯 CPU prompt eval 就要 33~150s。

验证三件事：
  场景 A  Web UI 同款路径手动切本地（force_local_mode）→ 纯对话模式生效、
          流式写故事全程无总超时、首 token 延迟恢复可用
  场景 B  主 API 连接层挂掉 → 自动降级到本地模型，tool_free_active 激活
  场景 C  Web SSE 管线真机贯通：reasoning/token/done 事件按序、渐进到达，
          done 后任务自动清理（前端 DeepSeek 式思考展示的数据源）

用法：
    python examples/e2e_local_chat_stream.py                # 默认 qwen3 4b（快，带思考）
    python examples/e2e_local_chat_stream.py --model richardyoung/qwen2.5-7b-instruct-abliterated
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.agent import Agent
from core.llm_client import FailoverClient, create_llm_client

OLLAMA = "http://localhost:11434/v1"
STORY_PROMPT = "请用三句话写一个温馨的小故事，直接开始写。"
CHAT_PROMPT = "你好，用一句话介绍你自己。"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class _DeadPrimary:
    """模拟主 API 连接层彻底挂掉（FailoverClient 靠异常触发自动降级）"""
    provider = "dead_primary"
    supports_vision = False
    model = "dead-model"
    tool_free = False

    def chat(self, messages, tools=None):
        raise ConnectionError("主 API 连接失败")

    def chat_stream(self, messages, tools=None):
        raise ConnectionError("主 API 连接失败")
        yield  # pragma: no cover


def build_failover(model):
    """工厂构建（与 Web UI / config.yaml 完全同款路径）：
    主 API 指向必挂端口（force_local 下永不真调），备用为本地 Ollama。
    工厂对本地端点自动 tool_free=true——这是本脚本要验证的关键默认值。"""
    return create_llm_client({
        "llm": {
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "x", "model": "primary-offline", "timeout": 3,
            "fallback": {
                "provider": "openai_compatible",
                "base_url": OLLAMA, "api_key": "", "model": model,
                "timeout": 300,
            },
        },
    })


def build_agent(model):
    fc = build_failover(model)
    agent = Agent(
        config={"llm": {"provider": "openai_compatible"},
                "agent": {"max_steps": 5, "confirm_high_risk": False}},
        llm_client=fc,
        confirm_handler=None,
    )
    return agent, fc


def scenario_a_force_local_story(model):
    print("=" * 64)
    print(f"场景 A：手动切本地模型写故事（Web UI /switch-fallback-model 同款路径）")
    print(f"  模型: {model}")
    agent, fc = build_agent(model)
    fc.force_local_mode(True)

    check("force_local 后 tool_free_active 激活（不传 63 个工具 schema）",
          fc.tool_free_active is True, f"tool_free_active={fc.tool_free_active}")

    reasoning_parts, token_parts, final_text = [], [], ""
    t0 = time.time()
    t_first = None
    stop_seen = False
    for chunk in agent.run_stream(STORY_PROMPT):
        if chunk.finish_reason == "reasoning":
            reasoning_parts.append(chunk.reasoning)
            if t_first is None:
                t_first = time.time() - t0
        elif chunk.finish_reason == "streaming":
            token_parts.append(chunk.text)
            if t_first is None:
                t_first = time.time() - t0
        elif chunk.finish_reason == "stop":
            stop_seen = True
            if chunk.text:
                final_text = chunk.text
    total = time.time() - t0
    if not final_text:
        final_text = "".join(token_parts)

    print(f"  首 token 延迟: {t_first:.1f}s | 总耗时: {total:.1f}s")
    print(f"  思考 token: {sum(len(p) for p in reasoning_parts)} 字 | "
          f"正文 token: {sum(len(p) for p in token_parts)} 字")
    print(f"  故事开头: {final_text[:60]}...")

    check("流式输出收到 stop 终态", stop_seen)
    check("故事正常生成（正文 ≥ 30 字）", len(final_text) >= 30,
          f"len={len(final_text)}")
    check("未触发超时报错", "超时" not in final_text)
    check("总耗时在可接受范围（< 180s，流式不受总超时限制）", total < 180,
          f"{total:.1f}s")
    if t_first is not None:
        check("首 token 延迟可用（< 60s；原带工具 schema 时 33~150s）",
              t_first < 60, f"{t_first:.1f}s")
    # qwen3 有独立思考通道，qwen2.5 没有——只报告不判失败
    r_chars = sum(len(p) for p in reasoning_parts)
    print(f"  [info] 思考通道字符数: {r_chars}"
          + ("（qwen3 独立 reasoning 字段，前端可渲染思考过程）" if r_chars else
             "（该模型无独立思考通道，属正常）"))


def scenario_b_auto_degrade(model):
    print("=" * 64)
    print("场景 B：主 API 连接层挂掉 → 自动降级本地模型")
    # 真实客户端把连接错误包装成文本返回（不抛异常），FailoverClient 靠异常降级，
    # 故用抛异常的桩模拟连接层硬故障；备用端点仍走工厂构建（tool_free 自动默认）
    local = create_llm_client({"llm": {"provider": "openai_compatible",
                                       "base_url": OLLAMA, "api_key": "",
                                       "model": model, "timeout": 300}})
    fc = FailoverClient(primary=_DeadPrimary(), fallback=local)
    agent = Agent(
        config={"llm": {"provider": "openai_compatible"},
                "agent": {"max_steps": 5, "confirm_high_risk": False}},
        llm_client=fc,
        confirm_handler=None,
    )
    t0 = time.time()
    resp = agent.run(CHAT_PROMPT)
    total = time.time() - t0

    print(f"  回复: {resp[:60]}...")
    print(f"  耗时: {total:.1f}s | degraded={fc.status['degraded']}")
    check("自动降级生效（degraded=True）", fc.status["degraded"] is True)
    check("降级后纯对话模式激活", fc.tool_free_active is True,
          f"tool_free_active={fc.tool_free_active}")
    check("本地模型正常回复（≥ 5 字）", len(resp) >= 5, f"len={len(resp)}")


def scenario_c_web_sse(model):
    print("=" * 64)
    print("场景 C：Web SSE 事件流真机贯通（/chat → 队列 → /task/<id>/events）")
    import miniyu_web

    agent, fc = build_agent(model)
    fc.force_local_mode(True)
    miniyu_web._ensure_agent = lambda: agent  # 脚本进程内替换，不碰真实配置

    client = miniyu_web.app.test_client()
    r = client.post("/chat", json={"message": STORY_PROMPT})
    task_id = r.get_json()["task_id"]
    print(f"  task_id: {task_id}")

    events = []
    t0 = time.time()
    resp = client.get(f"/task/{task_id}/events")
    for raw in resp.response:          # 逐块消费，记录真实到达时间
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.strip()
        if line.startswith("data: "):
            events.append((time.time() - t0, json.loads(line[6:])))

    types = [e["type"] for _, e in events]
    n_reasoning = types.count("reasoning")
    n_token = types.count("token")
    done = [e for _, e in events if e["type"] == "done"]
    total = time.time() - t0
    print(f"  事件序列: reasoning×{n_reasoning} → token×{n_token} → {types[-1]}")
    print(f"  事件总数: {len(events)} | 全程: {total:.1f}s")
    if events:
        print(f"  首个事件 {events[0][0]:.1f}s 到达，末事件 {events[-1][0]:.1f}s")

    check("收到 done 终态事件", bool(done))
    check("done 携带完整故事文本（≥ 30 字）",
          done and len(done[0].get("result") or "") >= 30)
    check("正文 token 事件 ≥ 5 个（逐字渐进，非一次性）", n_token >= 5)
    check("事件渐进到达（首事件 < 60s）", events and events[0][0] < 60,
          f"{events[0][0]:.1f}s" if events else "无事件")
    with miniyu_web._tasks_lock:
        check("done 后任务已清理（无泄漏）", task_id not in miniyu_web._tasks)
    if n_reasoning:
        print(f"  [info] 思考事件 {n_reasoning} 个 — 前端可渲染"
              f"「思考中(Xs)→已深度思考」折叠面板")


def main():
    model = sys.argv[sys.argv.index("--model") + 1] \
        if "--model" in sys.argv else "huihui_ai/qwen3-abliterated:4b"
    print(f"e2e 本地模型验证 | Ollama: {OLLAMA} | 模型: {model}\n")

    t_all = time.time()
    scenario_a_force_local_story(model)
    scenario_b_auto_degrade(model)
    scenario_c_web_sse(model)
    print("=" * 64)
    print(f"总计: {sum(RESULTS)}/{len(RESULTS)} 项通过 | 总耗时 {time.time() - t_all:.1f}s")
    if not all(RESULTS):
        sys.exit(1)
    print("全部通过 ✅  本地模型写故事不再超时，Web 端思考/正文流式展示数据链路完整。")


if __name__ == "__main__":
    main()
