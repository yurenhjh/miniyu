"""
e2e_bailian_search.py — 端到端验证：Agent 全链路百炼服务端联网搜索（真实 API，不计入 pytest）

验证链路：config.yaml web_search.enabled → create_llm_client 透传 → Agent 每步隐藏
browser_search → 请求体带 enable_search → 服务端注入实时资料 → 模型直接作答。

判定：
  1. agent.llm.server_web_search_active == True
  2. 模型可见工具里没有 browser_search（本地搜索已隐藏）
  3. 最终回复包含具体的实时信息（天气/新闻），而非"无法联网"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent
from core.agent_config import load_config

cfg = load_config()
agent = Agent(config=cfg)

model = getattr(getattr(agent.llm, "primary", agent.llm), "model", "?")
print(f"模型: {model}")
active = agent.llm.server_web_search_active
print(f"服务端联网搜索: {'✅ 生效' if active else '❌ 未生效（检查 config.yaml 顶层 web_search.enabled）'}")

full = agent._agent_openai_tools()
visible = agent._visible_tools(full)
names = [t["function"]["name"] for t in visible]
hidden = len(full) - len(visible)
print(f"模型可见工具: {len(visible)}/{len(full)}（隐藏 {hidden} 个），"
      f"browser_search {'✅ 已隐藏' if 'browser_search' not in names else '❌ 未隐藏'}")

question = "杭州今天天气怎么样？"
print(f"\n用户: {question}")
print("-" * 50)
answer = agent.run(question)
print(answer)
