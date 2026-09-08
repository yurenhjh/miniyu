"""
e2e_web_search_switch.py — 端到端验证：联网搜索总开关（DeepSeek 式）开/关两态（真实 API）

验证链路：config.yaml web_search.enabled → Agent 总开关 → create_llm_client 透传
→ _visible_tools 三分支裁剪 → _request_messages 提示注入 → set_web_search_enabled 运行时切换。

判定：
  开态：server_web_search_active=True，browser_search 隐藏、browser_extract 保留（62/63）
  关态：server_web_search_active=False，两个搜索工具都隐藏（61/63），离线提示注入
  真机：关态下问实时问题，模型不调隐藏工具、如实说明无法联网（而非编造）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent
from core.agent_config import load_config


def state(agent, label):
    full = agent._agent_openai_tools()
    names = [t["function"]["name"] for t in agent._visible_tools(full)]
    sysmsg = agent._request_messages()[0]["content"] if agent._request_messages() else ""
    hint = ("离线提示" if "离线模式" in sysmsg
            else "服务端搜索提示" if "服务端联网搜索" in sysmsg
            else "无联网提示")
    print(f"[{label}] 开关={'开' if agent.web_search_enabled else '关'} "
          f"服务端搜索={'生效' if agent.llm.server_web_search_active else '未生效'} | "
          f"可见工具 {len(names)}/{len(full)} "
          f"(browser_search {'藏' if 'browser_search' not in names else '显'}, "
          f"browser_extract {'藏' if 'browser_extract' not in names else '显'}) | {hint}")
    return names


cfg = load_config()
agent = Agent(config=cfg)
model = getattr(getattr(agent.llm, "primary", agent.llm), "model", "?")
print(f"模型: {model}\n")

state(agent, "1. 初始（config 默认开）")

agent.set_web_search_enabled(False)
state(agent, "2. 运行时切关")

agent.set_web_search_enabled(True)
state(agent, "3. 切回开")

# ---- 真机：关态下问实时问题，验证模型不调隐藏工具、如实说明 ----
agent.set_web_search_enabled(False)
question = "今天杭州天气怎么样？"
print(f"\n用户（关态）: {question}")
print("-" * 50)
answer = agent.run(question)
print(answer)
