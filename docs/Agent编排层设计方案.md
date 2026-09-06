# 第4组 · Agent 编排层设计方案（已实现：2026-09-06 真实 LLM qwen3.5-plus 真机验证）

> 状态：**已实现**。后端 = 阿里云百炼 qwen3.5-plus（DashScope compatible-mode），2026-09-06 已真机验证
> 多步文件系统工具调用；证据见 `docs/evidence/agent_qwen_live.md`。本文保留设计思路与关键接口，实现中按实际做了调整。
> 目标读者：本组自己 + 之后接手的 Claude 会话（开工前先读本文 + 文末"已落地决策"第 9 节）。
> 背景：课程 = Agentic OS，核心题眼是"自然语言 → Agent 自动调 OS 工具"。现有底层已很扎实
> （57 工具 / 25 技能 / 391 测试全过 / 真机 QQ·Edge·豆包 / Agent 可调组合技能 / 截图理解 / 邮件全链路 send_email）；彼时缺一层 LLM 驱动的"脑"，由本层补齐
> （后端 = 阿里云百炼 qwen3.5-plus，已真机验证）。
> 多组联合怕被拖累 → 老师建议多做能自证完整的亮点，本层即为此做。

---

## 1. 一句话目标

让用户输入一句自然语言（如"帮我整理桌面"），经 **LLM 驱动的 Agent 循环**自动拆成
`[sort_directory, create_directory, move_path, ...]` 并逐个执行、汇总成自然语言回复；
且整条链路 **可移植**——换机器、换 OS、没 key、没外网都能演示与验证。

---

## 2. 方案 1 范围（明确包含 / 暂不包含）

### 包含（核心闭环 + CLI）
| 组件 | 说明 | 大致工作量 |
|------|------|-----------|
| `LLMClient` 抽象 + **离线确定性脑（默认）** | 无 key 无网也能跑，Agent 循环可单测 | 中 |
| ReAct 型 Agent 循环 | 动态决策：指令+历史+工具清单 → 选工具 → 执行 → 回填 → 直到 done | 中 |
| 工具 → LLM 可读 schema（function-calling 格式） | 现有描述可复用，**参数 schema 需新增** | 中 |
| Conversation（对话记忆，滑动窗口） | 跨轮传上下文，防 token 超限 | 小 |
| CLI 对话入口（`examples/agent_cli.py`） | `input() → agent.run() → print()` 循环 | 小 |
| 结果自然语言化 | ReAct 收尾让 LLM 总结 → **随 2 免费** | 0 |

### 暂不包含（留给老师确认后再加）
- Web UI / Tkinter 聊天框（体验加分，非必需）
- Anthropic 原生客户端（先留接口位）
- 会话持久化到磁盘、自动摘要 summarize（记忆先做滑窗版）
- 多 Agent / 工具并行调度等高级编排

---

## 3. 复用现有资产（别重复造轮子）

| 现有资产 | 怎么复用 |
|---------|---------|
| `core/executor.py` 的 `ExecutionEngine.execute(plan)` | 当 Agent 的**确定性执行器**：Plan/PlanStep + `$ref` 跨步传参 + high 风险步骤确认门，现成 |
| `core/classifier.py` 的 `classify(text)` | 当**自然语言前置安全门**：先判意图/风险，决定 allow/warn/deny 与策略，再决定是否放 Agent |
| `core/safety.py` 确认门 + `TOOL_META`/`SKILL_META` | 每工具已有 `(risk, category, description)` 一句话描述 → schema 的描述字段直接取 |
| `mock/mock_tools.py` + `mock/mock_skills.py`（57/24 对等） | Agent 循环测试在无真 OS 下跑；与"离线脑"合起来 = 换机器验证的钥匙 |
| `core/os_service_api.py` 统一错误码 | Agent 步骤失败按 `TOOL_ERROR` 等回传，保持组间契约 |
| 双控制器 + CDP | 应用层 Win/Linux 抽象、浏览器 CDP 与 OS 无关 → 已具备跨 OS 底子 |

> 结论：**新写的是"脑 + 接线"（约 4~5 个新模块 + 1 示例 + 1 测试文件），不是推倒重做。**

---

## 4. 目标架构

```
用户(自然语言)
   │
   ▼
classifier.classify(text)─────────── 意图安全门(先判风险/策略，拒绝高风险话术)
   │ allow / warn
   ▼
Agent(ReAct 循环)   ◄────────── Conversation(滑窗记忆)
   │  call_llm(指令 + 历史 + 可用工具schema)
   ▼
LLMClient(抽象)
 ├─ DeterministicBrain   ← 默认/离线/无key：规则把意图→固定Plan（演示兜底+单测用）
 ├─ OpenAICompatible     ← 一个 base_url 覆盖 豆包/DeepSeek/Ollama/LM Studio/OpenAI
 └─ AnthropicClient      ← 预留接口位（本轮可不实现）
   │  返回 tool_call: {tool, arguments}
   ▼
Agent 决定：直接执行 或 交给 ExecutionEngine 按 Plan 执行
   │          （复用确认门：HIGH 工具先预览征求许可，拒绝→fail-safe）
   ▼
OSServiceAPI → ToolRegistry/SkillLibrary → OS / QQ / Edge …
   │  返回结果(原始 dict)
   ▼
回填到对话历史 → 循环，直到 LLM 返回 done
   ▼
Agent 让 LLM 把最终结果总结成自然语言 → 显示给用户
```

### ReAct 循环（已实现，流程如下）
```
def run(user_text):
    history.add("user", user_text)
    for _ in range(MAX_STEPS):                     # 上限防死循环
        resp = llm.chat(messages=history.get_window(),
                        tools=schema.of_available_tools())
        if resp.finish_reason == "tool":
            history.add("assistant", tool_calls=resp.tool_calls)
            for call in resp.tool_calls:
                out = execute_one(call.name, call.arguments)   # 经确认门
                history.add("tool", name=call.name, content=out)
        else:                                        # 普通文本 / done
            final_nl = resp.text
            break
    history.add("assistant", final_nl)
    return final_nl
```

---

## 5. 新增文件清单（建议命名，可再调）

| 文件 | 职责 |
|------|------|
| `core/llm_client.py` | `LLMClient` 抽象 + `DeterministicBrain` + `OpenAICompatibleClient`（+ `AnthropicClient` 桩） |
| `core/agent.py` | `Agent` 类：ReAct 主循环、步骤上限、确认门接线、结束判断 |
| `core/conversation.py` | `Conversation`：`messages` 列表、`add_message`、`get_window(n)` |
| `core/tool_schema.py` | 把 ToolRegistry + safety 元数据 → LLM tools 格式（含参数 JSON Schema 生成与覆盖表） |
| `core/agent_config.py` | 读环境变量/配置，集中机器相关项与默认值（可移植关键） |
| `examples/agent_cli.py` | CLI 聊天入口 |
| `tests/test_agent_loop.py` | fake LLM 驱动的循环/确认门/schema/降级测试 |
| `tests/test_llm_client.py` | 各 client 的 chat/tool 返回契约测试（离线脑重点测） |

---

## 6. 关键接口（已实现；与设计相比按实现做了调整）

```python
# core/llm_client.py
class LLMClient:                       # 抽象
    def chat(self, messages, tools=None):
        """-> ChatResponse(text | tool_calls, finish_reason)"""
    @property
    def name(self): ...

class DeterministicBrain(LLMClient):   # 默认；规则驱动，无key无网
    # 内置几类意图→Plan 的规则(整理/查找大文件/磁盘空间/建目录…)
    # 未知意图 → 返回提示文本而非瞎编工具调用

class OpenAICompatibleClient(LLMClient):
    # base_url 可指向 豆包(火山方舟)/DeepSeek/Ollama(本地)/OpenAI …
    # 走 chat.completions + tools 原生 function-calling

# core/agent.py
class Agent:
    def __init__(self, llm=None, api=None, confirm_handler=None,
                 max_steps=10, history_window=20): ...
    def run(self, user_text) -> str          # 返回自然语言结果
    def execute_one(self, name, arguments) -> dict   # 走安全/确认门
    # reuse: ExecutionEngine 执行确定性 Plan；classifier 做前置门

# core/tool_schema.py
def tools_for_llm(registry, os_capable=None) -> list[dict]:
    # 每工具: {type:"function",
    #          function:{name, description(取safety元数据), parameters:
    #                    {type:"object", properties:{...}, required:[...]}}}
    # os_capable: 过滤掉当前OS/环境不可用的工具(可移植关键)
    # 参数schema: 优先 inspect.signature 自动推导(类型+默认值→required)；
    #             特殊工具走 SCHEMA_OVERRIDES 覆盖表(如 run_command/manage_archive)

# core/conversation.py
class Conversation:
    messages: list[dict]
    def add_message(self, role, content=None, tool_calls=None, tool_call_id=None)
    def get_window(self, n=20) -> list[dict]   # 最近n条
```

---

## 7. 可移植性设计（换机器/换 OS/没 key 都能用）—— 4 条原则落地

### 原则 1：LLM 后端可插拔，默认离线脑
- 选型 = 环境变量，不写死：`AGENT_LLM_PROVIDER`（`deterministic` 默认 / `openai_compatible` /
  `anthropic`）、`AGENT_LLM_BASE_URL`、`AGENT_LLM_API_KEY`、`AGENT_LLM_MODEL`。
- 缺 key / 断网 / 没设环境变量 → 自动落回 `DeterministicBrain`，绝不崩。
- 离线脑不是"作弊"而是**可移植与可测的命脉**：完整闭环在任何机器都能演示 + 单测。

### 原则 2：工具清单按本机能力动态裁剪
- 只有"当前 OS/环境可用"的工具才进 LLM 的 tools 列表 → Linux/无QQ机器自然只有文件/系统工具，
  省 token，且不会点名调用不存在的工具。
- 注册面本来就是数据 → 提供 `os_capable` 过滤即可。

### 原则 3：机器相关项全部进 `agent_config`，带默认 + 优雅降级
把下列"绑死这台机器"的东西收敛，均可用环境变量覆盖、缺省自动探测：
| 现硬编码点 | 收敛后 |
|-----------|--------|
| `examples/qq_send_message_demo.py` 里 `DEFAULT_VISION = C:\Users\34808\.claude\skills\qwen-vision\vision.js`（已删） | 默认走**项目内视觉桥** `core/vision_bridge.py`（读 config.yaml 顶层 `vision_bridge` 段，可独立第二个视觉 key）；外部 node 桥仅作显式 `vision_js` / `AGENT_VISION_JS` 旧通道，不再是默认依赖 |
| Edge 安装路径、`%TEMP%\edge_db_profile`、端口 9333/9223 | `AGENT_BROWSER_PATH`/默认 `tempfile`/端口参数 |
| QQ 窗口布局分数 `_layout_profile` | 保留为布局常量（本就通用），与具体机器解耦 |
| 桌面/文档等用户路径 | `Path.home()` 展开，不写 `C:\Users\34808` |

### 原则 4：换机器 = `python -m unittest` 全绿 + 离线 CLI demo 跑通即证明
- `tests/test_agent_loop.py` 用 **fake LLM** 返回固定 tool_call，验证：循环顺序、$ref 传参、
  HIGH 工具确认门拒绝路径、步数上限、工具不可用时报错、无 LLM 时降级。
- 已有 mock 57/24 对等 → Agent 全链路不碰真 OS 也能测。
- 真机亮点（QQ/Edge/豆包）保持为**可选增强层**，脱离本机后代码自动降级，不影响演示完整性。

---

## 8. 安全设计（延续现有分层，不打折）
- **第一道**：`classifier.classify(text)` 前置意图/风险门（deny 直接挡）。
- **第二道**：Agent 内部工具调用仍走 `call_safely`：HIGH 风险工具先预览、征求许可，拒绝即 fail-safe
  （沿用 `safety.ConfirmationDenied`）。
- Agent 层只把 LLM 当"计划建议方"，**参数合法性/路径安全仍由 ToolRegistry/safety 侧校验**，LLM 不可绕过。
- 不把 API key 打进代码/日志；`agent_config` 从环境变量读。

---

## 9. 关键决策（原"待问老师"清单，2026-09-06 已定并落地）
1. **范围**：已做完整闭环——核心 Agent 循环 + CLI（`examples/agent_cli.py`）+ Web UI（`examples/miniyu_web.py`），演示/答辩不需要再另加。
2. **真实 LLM 后端与账号**：已定并落地 = **阿里云百炼 DashScope（`compatible-mode`），模型 `qwen3.5-plus`**；
   base_url 带 `/v1`；key 经环境变量 `AGENT_LLM_*` 注入，**未写入仓库任何文件**；真机验证见 `docs/evidence/agent_qwen_live.md`。
3. **"离线确定性脑"的定位**：落地为**双模式**——默认 `deterministic` 离线脑（零依赖，可演示/单测），
   切 `openai_compatible` 即真实 function-calling；非流式与流式两种真机均已跑通。
4. **确认门节奏**：落地为**按授权档位确认**——HIGH 工具经安全确认门（CLI 弹确认 / Web 模态框），用户允许/拒绝；
   档位 `agent.authorization`：base=全部 HIGH 需确认（默认=现状）；advanced=仅永久删除文件需确认；full=从不确认。
   旧 `agent_config.confirm_high_risk`（默认 True）保留为兼容开关（false ⇔ full）。
5. **function-calling 形态**：落地为双通道——**原生 tools/tool_use** 为主，另加"LLM 输出 JSON 文本再由本地解析"兜底
   （JSON 兜底为真机修复项，见进度日志 2026-09-06）。
6. **组间契约**：已定——第4组改**自研自证**、不再依赖第 3 组联调；Agent 层错误码沿用组内 `OSServiceAPI` 契约（`TOOL_ERROR` 等回传）。
7. **记忆**：落地为滑窗 + **多会话管理 + 磁盘持久化 + 超窗自动摘要压缩**（`core/conversation.py` 的 `SessionManager`/`_compress_memory`），可续聊。
8. **组合技能暴露给模型（Agent 白名单，2026-09-06）**：SkillLibrary 的高层技能（如 `app_send_message`「QQ 搜索+发送」）以 **OpenAI function** 形式并入模型可见的 tools（`57 底层工具 + 白名单技能`）；`Agent._execute_one` 拆 `_execute_tool`/`_execute_skill` 两路，技能同样走 HIGH 确认门。因为 tool_call 无法携带 Python 闭包，技能本体提供 `verify_ocr=True` 让内部自动构造「屏幕 OCR 核对目标会话」门（`make_ocr_verify` → 裁剪聊天标题横带 → 项目内视觉桥 `core.vision_bridge`，读 config.yaml 的 `vision_bridge` 段；主对话有视觉则直接用主模型读图），找不到视觉源明确报错、绝不盲发；发送类技能在 Agent 分发时默认强制 OCR 门。配套单测 `tests/test_agent_skills.py`（不碰真 QQ / 真实视觉桥）。
9. **Agent 截图理解 + 过程产物生命周期（2026-09-06，ADR 0009）**：视觉通道两路自适应——视觉模型（`supports_vision=True`，qwen3.5-plus 原生多模态已真机探测通过）截图以真 base64 独立观测消息回传；无视觉模型走项目内视觉桥（config.yaml 的 `vision_bridge` 段）转文字。修掉"截图路径被当 base64"坏图 bug；新增 Agent 自带能力函数 `screen_inspect(question)`（只读、无确认门，SYSTEM_PROMPT 规则 7 指导"不确定/出错先截图看"），GUI 成功/失败都自动补观测图。截图等过程产物单独存会话产物目录 `<artifacts_root>/<会话>`（`AGENT_ARTIFACTS_DIR` > `config.memory.artifacts_dir` > `%TEMP%\miniyu_artifacts`，可被下一技能复用）；用户说「清理截图/清理产物」或 `/reset` 联动清空；本轮产生产物时最终回复附一句提醒。配套单测 `tests/test_agent_vision.py`，测试 322 → **332 全绿**。
10. **统一视觉源 + 可移植（2026-09-06，与决策 8/9 配套）**：看图/OCR 到底用谁，只由 `llm.supports_vision` 决定——`true`（主对话有视觉，如 qwen3.5-plus）→ 直接调主对话(llm)段读图，**不必配第二个 key**；`false`（主对话纯文本）→ 调 config.yaml 顶层 `vision_bridge` 段（独立第二个视觉 API，可与 llm 不同 key/厂商）。这统一收敛在新增的 `core/vision_bridge.py`（`require_vision` 校验 + `describe_image(image, question)` 自动选源），`make_ocr_verify` 与 `screen_inspect` 文字通道都经它。可移植性：删除 demo 里 `C:\Users\34808\...\qwen-vision` 本机绝对路径依赖，新增 `config.yaml.example` 模板（两种填法：单 key 有视觉主模型 / 双 key + 视觉桥）；外部 node 桥仅作显式 `vision_js`/`AGENT_VISION_JS` 的旧通道保留。两源都没配/失败 → `VisionBridgeError` 给配置指引，绝不瞎编。测试 **332 → 343 全绿**。
11. **邮件能力 = 自验证技能 send_email（2026-09-06，决策 8 同形态的再落地）**：用户问能否补「微信和写邮件」后评估：微信真号自动化封号风险不可逆、不做；邮件走 SMTP/IMAP 全链路。做成 **1 个白名单组合技能**（不是加底层工具——工具 57 不变、技能 24→25）：新增纯 stdlib 协议层 `core/email_client.py`（`smtp_send` / `imap_verify_sent` / 可选 `imap_verify_arrival`；连接拆 `_connect_smtp/_connect_imap` 内部缝隙、patch 即可单测、不真联网）。skill 内部完成「发 → IMAP 回读发件箱『已发送』 →（配 verify_inbox 时）轮询收件人收件箱『确实到达』」的**自验证闭环**，回读未命中如实报 found=False、不把没验到当成功；email 段没配好（缺 username/auth_code）在『发之前』抛 `MailError` 中文指引（fail-closed）；授权码同 `llm.api_key` 同级敏感，只进 config/env、不入库。配套 `tests/test_email.py` 25 例、全量 **343 → 368 全绿**；`examples/email_demo.py`（直驱技能 / `--agent` 真实 LLM）。**QQ 真机实跑（2026-09-06）**修正三处协议层假设并补 2 条回归：①QQ 投递改写 Message-ID（`<…@miniyu.local>` → `<tencent_…@qq.com>`）②中文主题在存储头里是 RFC2047 编码 → `_folder_search_ids` 改为解码头字段后按主题兜底匹配 ③『已发送』文件夹实为带空格的 `Sent Messages`（非 `&XfJT0ZAB-`），imaplib 不加引号直接 EXAMINE 会 BAD → 按 RFC3501 加引号；另发现 **QQ 授权码 SMTP 不在发件箱留副本** → 发件箱未命中≠没发，双端闭环下以收件箱到达核验为铁证（163/Gmail 等留副本仍可查）。测试 **368 → 370 全绿**（test_email 25→27）；证据 `docs/evidence/email_live_qq.md`。
12. **授权档位 = 确认门三档可切换（2026-09-06，决策 4 的升级落地）**：用户嫌确认弹窗麻烦，要"像 Claude Code / Trae 授更广权力、只在删除文件时才需确认"。把"high 即确认"升级为纯函数 `should_confirm(level,name,risk)`（`core/safety.py`）：base（默认，现状）全部 HIGH 确认；advanced 只对**永久删除**类（`delete_file`/`delete_directory`/`cleanup_by_type`/`empty_trash`）确认，`run_command`/`kill_process`/`terminate_process`/`app_send_message`/`send_email` 自动放行；full 从不确认（含删除）。`resolve_authz_level(agent_cfg)` 统一解析：`agent.authorization` 优先，旧 `confirm_high_risk=false` ⇔ full（配置不回归）。Agent 运行时确认门（`_execute_tool`/`_execute_skill`）读 `self.authz_level`；引擎级 fail-safe（`call_safely`/`run_skill_safely`/离线 executor）保持按 HIGH 返回 `CONFIRMATION_REQUIRED` 不变——档位只作用于 Agent 交互层。UI：Web 顶栏授权下拉 → `/switch-authz`（`set_config_authorization` 文本级写回 config.yaml + 热改运行中 agent 不丢会话；切 full 先弹一次浏览器 confirm），CLI 加 `/authz [base|advanced|full]`。QQ 发送的屏幕 OCR 门在 advanced/full 下仍生效（**自动放行 ≠ 盲发**）。配套 `tests/test_authz.py` 21 例 → **370 → 391 全绿**（15 个测试文件）。

---

## 10. 实施里程碑（M1~M5 已于 2026-09-06 落地；M6 大部分完成，ADR 待补）
- [x] M1 脚手架 + schema：`agent_config`、`Conversation`、`tool_schema`（自动推导+覆盖表）+ 对应测试 —— 证据：`core/agent_config.py`、`core/conversation.py`、`core/tool_schema.py`；`tests/test_agent_config.py`（6）、`tests/test_conversation.py`（28）
- [x] M2 LLM 层：`LLMClient` 抽象 + `DeterministicBrain`（"整理桌面/磁盘空间/找大文件"等示例意图）+ `OpenAICompatibleClient` + 测试 —— 证据：`core/llm_client.py`；`tests/test_llm_client.py`（30，含 JSON 兜底/回退/工厂/响应格式）
- [x] M3 Agent 循环：`Agent.run`/`run_stream` + `execute_one`，接确认门 + 步数上限；fake LLM 单测 —— 证据：`core/agent.py`；`tests/test_agent.py`（16，含多工具 tool_call_id 配对、流式终止）
- [x] M4 CLI/交互：`examples/agent_cli.py` + `examples/miniyu_web.py`，离线脑完整跑通"帮我整理桌面"类指令（换机可演示）
- [x] M5 真实后端：已定 = 阿里云百炼 `qwen3.5-plus`（DashScope compatible-mode）；`OpenAICompatibleClient` 接线 + 双模式开关 + 真机验证 —— 证据：`docs/evidence/agent_qwen_live.md`；`examples/live_llm_check.py` 独立核验 5/5
- [ ] M6 收尾（大部分完成）：README/进度跟踪/报告素材 已同步，evidence 已新增 `agent_qwen_live.md`；ADR 已补 `0009`（截图理解+过程产物生命周期），schema 生成 & Agent 循环决策类 ADR 仍可后续补充

## 11. 验收标准（写完自查）
1. `python -m unittest discover -s tests` 全绿（含新增 agent 测试）。
2. 不设任何环境变量时 `python examples/agent_cli.py` 能离线跑通"整理桌面/看磁盘空间"并回自然语言。
3. 在有 API key 机器上切 `AGENT_LLM_PROVIDER=openai_compatible` 能真 function-calling 闭环。
4. 把仓库拷到一台无 QQ/无 vision/（甚至 Linux）的机器：测试全绿 + 离线 demo 通，不因缺本机资源崩。
