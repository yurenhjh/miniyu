# Group4 - miniyu 桌面 AI 助手（Tool Registry + OS Skills + Agent 编排层）

## 完整桌面 AI 助手（LLM 驱动，function-calling 调用 59 个系统工具 + 26 个技能）

---

## 1. 快速开始

### 开箱即用（离线模式，零依赖）

```bash
# 终端界面
python examples/agent_cli.py

# Web 界面（需 pip install flask）
python examples/miniyu_web.py
```

### 使用真实 LLM

```bash
# 设置环境变量（或编辑 config.yaml）
set AGENT_LLM_PROVIDER=openai_compatible
set AGENT_LLM_BASE_URL=https://api.openai.com/v1
set AGENT_LLM_API_KEY=sk-xxxx
set AGENT_LLM_MODEL=gpt-4o-mini

# 启动
python examples/agent_cli.py
```

### 可移植性（Windows + Linux 双平台，可发给他人）

整个项目使用**相对路径**（无硬编码绝对路径）+ `pathlib` 跨平台路径 + `platform.system()` 自动适配，核心（工具注册 / OS Skills / Web CLI）在 Windows 与 Ubuntu 上同一套代码运行；应用/浏览器控制层为双实现（`app_controller` 分 `WindowsAppController` / `LinuxAppController`，`browser_controller` 用跨平台 CDP）。

**给别人部署（他的机器、他的 key，只用 4 步）：**
```bash
# 1. 装依赖（config.yaml 里的 key 是你自己的、不入库，别人拿到的是空模板）
pip install -r requirements.txt

# 2. 从模板生成自己的配置并填 key（关键！别用我仓库里那份）
cp config.yaml.example config.yaml
#    编辑 config.yaml：填你自己的 llm.api_key / llm.model；主模型无视觉再填 vision_bridge；
#    要发邮件填 email 段；要本地降级装 Ollama 并填 fallback 段。

# 3.（Linux 桌面自动化）装系统工具 + 脚本一键配置
bash setup_linux.sh          # 装 xdotool/wmctrl/xclip/gnome-screenshot + python 依赖 + 生成配置

# 4. 启动（Windows 用运行 .bat，Linux 用 .sh）
python examples/miniyu_web.py    # 或 run_miniyu_web.bat / bash run_miniyu_web.sh
```

**语音输入浏览器要求：一键语音（🎤）用浏览器原生 Web Speech API**，仅 **Chrome / Edge** 支持；Windows 上**推荐 Edge**（走微软语音服务、国内可用），Chrome 走谷歌服务大陆常连不上。其余浏览器自动隐藏该按钮、不影响文字输入。

> **近期变更（2026-09-09）**
> - **第 4 组提交文档补齐（docs/）**：对照任务设计书评分/验收，新增 5 份可提交文档——`docs/第4组调研报告.md`、`docs/第4组设计文档.md`、`docs/第4组单元测试报告.md`、`docs/第4组联调与集成报告.md`、`docs/第4组交付说明.md`（含提交/打包清单与一键复现）。功能面核对第 4 组要求**无缺失且超额**（59 工具 + 26 技能 + 调用统计 + 工具签名 + 57 Mock + 512 测试全绿），本轮无需改代码。
> - **第 5 组职责补齐（整体设计完整性）**：对照设计书「系统协调+安全+RAG」核查，安全层早已完整，**补齐 4 项缺失**——新增 `core/coordinator.py`：`SystemCoordinator`（模块编排 1→5→2→3→4 数据流）+ `SecuritySandbox`（四层安全：权限/沙箱/签名/隐私）+ `RAGKnowledgeBase`（轻量向量检索执行轨迹，零依赖）+ `AuditLog`（审计日志可落盘）+ `MockCoordinator/MockRAG/MockAudit`（降级 Mock）。已**接入 Agent**：`run/run_stream` 结束自动记审计 + 存 RAG 轨迹（`agent.coordinator.enabled` 可关，默认开）。新增 27 项测试全绿。
> - **调研报告补字达标 + 汇报 PPT + PDF 版**：`docs/第4组调研报告.md` 汉字 2453 → **约 4000 字**（≥3000 达标，补「与第 5 组协同调研」「MCP 演进」「对标 Claude Computer Use」「调研局限与后续工作」章节）；新增 `docs/第4组汇报PPT.pptx`（**18 页 ≥15 页**，检查点 4 汇报用）；新增 `docs/第4组调研报告.pdf`（**正文宋体小四号**，由 Edge headless 打印，脚本 `docs/scripts/md_to_pdf.py` 可复现，`.md` 源文件保留）。
> - **Web 6 场景实测演示全通过**：启动 web（http://localhost:5000）驱动真实 LLM qwen3.7-flash，设计书演示场景表 6 个场景**全部一次跑通**——打开文件管理器、导航目录、建文件夹、编辑文本、窗口切换、整理下载目录（14 步工具链自主完成扫描→分类→建夹→移动）；均经磁盘实体验证。新增 `docs/第4组Web演示记录.md` + 演示脚本 `examples/demo_web_scenarios.py`。
>
> **近期变更（2026-09-09）**
> - **Linux（Ubuntu）可移植性落地 + 他人部署**：核心代码本就双平台（相对路径 / pathlib / `platform.system()` 适配；`app_controller` 分 Windows/Linux 实现；`browser_controller` 跨平台 CDP）。本轮新增：① `setup_linux.sh`（一键装 `xdotool/wmctrl/xclip/gnome-screenshot` 等系统依赖 + Python 依赖 + 从模板生成 `config.yaml` + 检测 Wayland/Xorg）；② `run_miniyu_web.sh` / `run_miniyu_cli.sh`（Linux 启动脚本，等价于 .bat）；③ `LinuxAppController` 自动检测 **Wayland 会话**并提示切 Xorg（xdotool/wmctrl 是 X11 工具，Wayland 下桌面自动化不可用，仅文件管理/Web 聊天不受影响）；④ `.gitattributes` 锁定 `.sh` 为 LF 行尾；⑤ `config.yaml.example` 补齐 `web_search.code_interpreter` 新字段。**发给他人**：他的机器 `cp config.yaml.example config.yaml` 填自己的 key 即可完整使用，你的 key/本地模型不随代码分发。
>
> **近期变更（2026-09-08）**
> - **百炼服务端代码解释器（数学计算/数据分析）**：纯计算回合（如「123的21次方是多少？」）自动启用百炼云端 Python 沙箱（`enable_code_interpreter`），模型不再硬算或乱报，返回**逐位精确**结果（实测 123²¹ 全对）；涉及本地文件/系统操作的提问照常走本地工具，两者自动分流。模型不支持时客户端自动探测停用，不影响正常对话（开关 `web_search.code_interpreter`，默认开）。
> - **前端布局锁定 + 工具调用折叠面板**：页面整体**固定于视口**（html/body 禁滚动、禁横向溢出），滚动只发生在聊天区内部——整个界面不再能被划出窗口；工具调用改成像深度思考一样的**折叠面板**——默认收起为一行「工具调用 (N 个) ▼」，点标题展开/再点收起，展开后**限高 260px、超出在框内滑动查看**，不再占据大片输出区。
> - **性能调优（省 token / 省延迟）**：① 工具结果**超长自动截断**（默认保留前 4000 字符并附说明，模型可再针对性读取）——`run_command` 输出、文件内容等大结果不再全量回传 LLM；② **HTTP 连接池复用**（requests.Session 按线程隔离）——省掉每次请求的 TCP/TLS 握手（约 50-200ms）。
> - **Web 思考内容折叠（DeepSeek/Trae 式）**：深度思考回复完成后默认折叠成一行「已深度思考（用时 X 秒）▼」，点标题展开/再点收起；展开后内容框**限高 300px、可滚动**看全部。
> - **历史会话侧栏增强**：每次启动**新开一个空对话**（历史仍在侧栏）；侧栏按**最近访问**排序并显示时间（今天 HH:mm · N条）；点击会话可在聊天区回看该历史对话；超过 20 条先显示最近 20、可点「加载更多」展开全部。
> - **本地模型乱码修复 + 停止生成按钮**（09-07，详见进度跟踪）：Ollama 流式按 UTF-8 增量解码；生成中「发送」变红色「⏹ 停止」可秒级打断。
> - **`.bat` 改为纯 ASCII 英文**（`run_miniyu_web.bat` / `run_miniyu_cli.bat`）：原 UTF-8 中文在 cmd 旧代码页(GBK)下双击报 `'潰' 不是内部或外部命令` 乱码，已彻底解决。
> - **使用说明新增必读**：首次使用/给别人部署前需换成**自己的 API key 与模型**；想用本地模型需**自行下载**（详见 `miniyu使用说明.md` 第 2 节）。
> - **语音输入 🎤**（Web UI）：输入框左侧麦克风按钮，点击开始倾听、再点关闭，识别文字自动填入输入框。纯浏览器原生（Web Speech API），零后端零安装；建议用 **Edge** 打开（走微软语音服务、国内可用），Chrome 走谷歌服务大陆常连不上。识别为**持续倾听**（停顿不结束，点 🎤 才关），多次识别结果**追加**在已有文字后不互相覆盖。
> - **历史会话自动标题**：会话标题自动取**用户首条消息的第一句**（首个标点前的内容，超 20 字截断加 …），不再统一叫"新对话"；加载旧会话时若标题仍是旧默认"新对话"，也会用其首条用户消息自动迁移重命名。不依赖 LLM，离线/确定性脑同样生效。

---

# 2. 项目简介

本项目为课程设计 **第4组：miniyu 桌面 AI 助手**（LLM 驱动，function-calling 调用 59 个系统工具 + 26 个技能），底层以"工具注册 + OS Skills（Tool Registry + OS Skills）"作为统一系统能力接口。

本模块负责为 Agentic OS 提供统一的系统能力接口，包括：

- 工具注册管理
- 文件系统操作
- 系统命令执行
- 操作系统技能封装
- Mock 接口支持（跨组联调）
- 调用统计分析
- **工具描述 Schema 自动生成**（ToolSpec + 多格式转换）
- **MCP 协议兼容接口**（tools/list / tools/call 格式对齐）
- **系统管理工具**（进程管理 / 网络 / 环境变量）
- **工具签名验证**（完整性校验）
- **Agent 编排层**（LLM 驱动的 ReAct 循环，支持离线脑 + 真实 LLM 双模式）

本模块位于 Agentic OS 架构中的 **Tool Layer（工具能力层）**，为上层 Agent（第3组 AppAgent）提供标准化工具调用能力。

---

# 2. 系统架构

整体调用流程(完整链路，第4组独立可演示)：

```
            User (自然语言指令)
                    |
                    v
         Agent (ReAct 循环)               ← 新增：Agent 编排层
            ├── DeterministicBrain (离线脑，零依赖)
            └── OpenAICompatibleClient (真实 LLM)
                    |
                    v
              OSServiceAPI                  ← 统一入口
                    |
        -------------------------
        |                       |
        v                       v
  ToolRegistry             SkillLibrary      ← 核心模块
        |                       |
        v                       v
 File/System Tools          OS Skills        ← 具体实现
        |
        v
   Operating System
```

---

# 3. 核心设计

## 3.1 工具统一管理（ToolRegistry）

通过 `ToolRegistry` 实现：

- 动态工具注册 / 注销
- 工具查询
- 工具调用（含参数校验）
- 异常处理
- 调用日志记录
- **调用统计**（各工具调用次数、成功率、Top5排名）
- 日志清空

## 3.2 OS能力抽象（SkillLibrary）

将底层系统操作封装为高级技能：

```
Tool（基础能力）
    ↓
Skill（高级任务能力）
```

例如：
- `move_path()`（Tool）→ `organize_downloads()`（Skill）
- `run_command()`（Tool）→ `system_info()`（Skill）

## 3.3 统一接口（OSServiceAPI）

对外暴露两个核心方法：
- `execute_tool(tool_name, params)` — 调用系统工具
- `run_skill(skill_name, params)` — 调用系统技能

## 3.4 Mock支持

开发阶段通过 Mock 模块模拟真实系统操作：

- 不依赖真实文件系统
- 支持跨组并行开发
- 保证接口稳定一致
- 覆盖全部59个工具和24个技能

## 3.5 工具描述 Schema（ToolSpec）

为每个工具自动生成**结构化描述**，包含参数名、类型、描述、默认值。支持一键转换为三种主流 Agent 工具格式：

- **MCP 格式** — `name` + `description` + `inputSchema`（JSON Schema）
- **OpenAI 格式** — `type: "function"` + `function.parameters`
- **Anthropic 格式** — `name` + `description` + `input_schema`

上层 Agent 调用 `list_tools_mcp()` 即可获取完整工具定义，直接传递给 LLM 的 tool calling 能力实现动态决策。

## 3.6 系统管理工具

在原文件操作工具基础上，新增**系统管理工具集**（基于 psutil）：

| 工具 | 说明 | 风险 |
|------|------|------|
| `list_processes` | 列出所有进程（支持排序/过滤） | 只读 |
| `get_process_info` | 获取进程详细信息 | 只读 |
| `kill_process` | 强制终止进程 | 高危 |
| `terminate_process` | 优雅终止进程 | 高危 |
| `network_status` | 网络接口状态 | 只读 |
| `ping_host` | 测试网络连通性 | 只读 |
| `list_env_vars` | 列出环境变量 | 只读 |
| `get_env_var` | 获取环境变量值 | 只读 |

## 3.7 工具签名校验

注册工具时自动计算函数字节码的 SHA256 签名，支持：

- 查询工具签名：`get_tool_signature(name)`
- 验证工具完整性：`verify_tool_integrity(name)`
- 批量列出签名：`list_tool_signatures()`

用于防止工具函数在运行时被意外篡改。

## 3.8 浏览器结构化控制（CDP）

在原桌面键鼠模拟之外，新增**浏览器结构化控制**能力。...（后续内容不变）

- **browser-use** —— 把页面压成“可交互元素索引清单 + 索引动作”，规避 LLM 选择器幻觉与 DOM 过大的问题；采用事件驱动的分层架构。
- **Playwright MCP** —— 基于可访问性树（而非像素）生成结构化快照，用元素引用 `ref` 让 LLM 精确定位，无需视觉模型。
- **browserclaw / OpenClaw** —— “snapshot + ref 定位”：先生成 AI 可读文本树，再按 ref 引用操作元素。

本组 `BrowserController` 的设计对齐这些思路：

| 设计要点 | 说明 |
|---------|------|
| 快照索引定位 | `browser_snapshot()` 给可交互元素打 `data-agentic-idx` 标记，之后 `click/type` 按 index 操作（等价于 Playwright MCP 的 `ref`、browserclaw 的 ref） |
| 中文安全输入 | 走 CDP `Input.insertText` 直接向焦点元素插文本，无需剪贴板 |
| 事件驱动等待 | `wait_for()` 轮询元素/文本出现，替代盲目 sleep |
| 跨平台 | 浏览器屏蔽 OS 差异，无需 Windows/Linux 双实现 |
| 惰性初始化 | 首次调用才实例化 `BrowserController`，不拖累非浏览器场景 |

---

## 3.9 Agent 编排层 — miniyu

miniyu 是第4组独立实现的 AI 桌面助手，通过 LLM 驱动的 Agent 循环，使整个系统不依赖第1~3组即可独立演示完整链路。

### 双界面

| 界面 | 文件 | 说明 |
|------|------|------|
| **终端界面（CLI）** | `examples/agent_cli.py` | 零依赖，任何机器都能跑，miniyu 品牌提示符 |
| **Web 桌面客户端** | `examples/miniyu_web.py` | Flask + 深/浅双主题（顶栏 ☀️/🌙 一键切换、localStorage 记忆），高危操作弹模态框确认；顶栏可切换授权档位（基础 / 高级 / 全自动）；**可搜索模型下拉框**：在线模型点选即热切换（先实测连通），本地 Ollama 模型点选即切离线模式；**KaTeX 数学公式渲染**（`$...$`/`$$...$$` → 正常排版，KaTeX/marked/DOMPurify 本地打包离线可用） |

### 架构

```
用户输入 "帮我整理桌面"
    ↓
 Conversation（对话记忆，滑动窗口 20 条）
    ↓
 Agent（ReAct 循环）
    ├── LLMClient（抽象基类）
    │   ├── DeterministicBrain（离线脑，规则匹配，零依赖，默认兜底）
    │   ├── OpenAICompatibleClient（真实 LLM，兼容 GPT/DeepSeek/豆包/Ollama 等）
    │   └── FailoverClient（自动降级：主 API → 本地 Ollama → 确定性脑；
    │       支持运行时热切换模型 + force_local 手动本地模式）
    ↓
 OSServiceAPI（59 个工具 + 26 个技能）
    ↓
 实际执行
```

### 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| 配置管理 | `core/agent_config.py` | YAML 配置文件 + 环境变量覆盖 |
| 对话记忆 | `core/conversation.py` | 滑动窗口，支持多模态（图片） |
| 离线脑 | `core/llm_client.py` → `DeterministicBrain` | 关键词匹配 20+ 意图，零依赖 |
| 真实 LLM | `core/llm_client.py` → `OpenAICompatibleClient` | 兼容 OpenAI 格式，支持视觉模型 |
| 自动降级 | `core/llm_client.py` → `FailoverClient` | 主 API 失败自动切本地 Ollama（5 分钟重试恢复）；`force_local_mode()` 手动切纯本地模式 |
| ReAct 循环 | `core/agent.py` → `Agent` | 安全确认门、最大步数保护、GUI 截图回传；换模型自动清理历史中的工具调用消息（跨模型无缝接力） |
| CLI 入口 | `examples/agent_cli.py` | 交互式对话，支持 /help /reset /tools 等命令 |

### 配置方式

用户只需编辑 `config.yaml`，无需修改代码：

```yaml
# 离线模式（默认，零依赖）
llm:
  provider: deterministic

# 或接真实 LLM：
# provider: openai_compatible
# base_url: "https://api.openai.com/v1"
# api_key: "sk-xxx"
# model: "gpt-4o-mini"
```

环境变量优先级更高，可临时覆盖：

```bash
set AGENT_LLM_PROVIDER=openai_compatible
set AGENT_LLM_BASE_URL=https://api.deepseek.com
set AGENT_LLM_MODEL=deepseek-chat
```

### 使用方式

```bash
# 离线模式（默认，零依赖）— 终端界面
python examples/agent_cli.py
# 或双击 run_miniyu_cli.bat

# 离线模式 — Web 界面（需 pip install flask）
python examples/miniyu_web.py
# 或双击 run_miniyu_web.bat

# 真实 LLM 模式（终端或 Web 都可）
set AGENT_LLM_PROVIDER=openai_compatible
set AGENT_LLM_BASE_URL=https://api.openai.com/v1
set AGENT_LLM_API_KEY=sk-xxx
python examples/agent_cli.py
```

---

# 4. 项目结构

```
group4_tools_os_skills/
│
├── core/                          # 核心模块
│   ├── __init__.py
│   ├── tool_registry.py           # 工具注册表（正式版，59 工具，含 click_at + 系统管理 + 编程辅助）
	│   ├── tool_schema.py           # 工具描述 Schema（ToolSpec，多格式转换）
	│   ├── skill_library.py           # OS Skills 技能库（26 技能，含 app_*/browser_*/QQ读 read_qq_chat/邮件 send_email）
	│   ├── app_controller.py          # 应用操作控制器（Windows/Linux；click_at / read_clipboard / get_window_rect）
	│   ├── browser_controller.py      # 浏览器结构化控制（CDP：snapshot 索引 + insertText 中文输入）
	│   ├── safety.py                  # 安全分级元数据 + 确认门（call_safely / run_skill_safely）
	│   ├── classifier.py              # 任务分类器（auto / confirm / warn）
	│   ├── executor.py                # 多步执行引擎（Plan / ExecutionEngine，支持 $ref 取上步结果）
	│   ├── os_service_api.py          # 对外统一接口
	│   ├── utils.py                   # 跨平台工具函数（safe_print 等）
	│   ├── agent_config.py            # Agent 配置管理（YAML + 环境变量覆盖）
	│   ├── llm_client.py              # LLM 客户端（离线脑 + OpenAI 兼容）
	│   ├── agent.py                   # Agent ReAct 循环
	│   └── conversation.py            # 对话记忆（滑动窗口 + 多模态支持）
│
├── mock/                          # Mock模块（跨组联调用）
│   ├── __init__.py                # Mock统一入口 + MockOSServiceAPI
│   ├── mock_tools.py              # Mock版工具注册表（59个工具）
│   └── mock_skills.py             # Mock版技能库（26个技能）
│
├── tests/                         # 单元测试（共 512 个，全部通过）
│   ├── __init__.py
│   ├── test_tool_registry.py      # ToolRegistry测试（92个：全部工具+新工具+异常+别名+错误码）
│   ├── test_skills.py             # SkillLibrary测试（43个：基础+扩展+搜索+Agent）
│   ├── test_os_service_api.py     # 接口联调测试（11个）
│   ├── test_mock.py               # Mock模块测试（33个）
│   ├── test_app_tools.py          # 应用操作/浏览器工具与技能测试（19个）
│   ├── test_agentic.py            # 安全分级/确认门/执行引擎/任务分类测试（19个）
	│   ├── test_agent_config.py       # 配置管理测试（6个）
	│   ├── test_conversation.py       # 对话记忆测试（28个）
	│   ├── test_llm_client.py         # LLM客户端测试（30个）
	│   └── test_agent.py              # Agent编排层测试（16个）
	│   ├── test_model_switch.py       # Web模型切换测试（12个）
	│   ├── test_agent_skills.py      # Agent可调组合技能：技能→模型函数 / 技能分发 / OCR门（17个）
	│   ├── test_agent_vision.py      # Agent截图理解(视觉双通道)/统一视觉源/过程产物生命周期测试（19个）
	│   ├── test_authz.py              # 授权档位（base/advanced/full）确认门测试（21个）
│   ├── test_email.py              # 邮件全链路：SMTP 发信/IMAP 回读核验 + Agent 分发（27个）
│   ├── test_agent_read_web.py     # QQ『先读后回』read_qq_chat + 联网 browser_* 白名单（11个）
│   ├── test_web_http.py           # HTTP 联网搜索 core/web_search 离网单测：解析清洗/相对链接过滤/摘要截断/异常/瞬断重试（11个）
│   ├── test_tool_protocol.py      # 工具协议自适应：文本协议解析/400双分支/流式适配/Failover透传/切换重置（19个）
│   ├── test_bailian_tools.py      # 百炼服务端联网搜索 enable_search + 联网总开关三态（19个）
│   ├── test_tool_free_sse.py      # 本地模型纯对话 tool_free + Web SSE 事件队列管线（21个）
│   └── test_stream_stop_encoding.py # 流式 UTF-8 增量解码（乱码修复回归锁）+ 停止生成（11个）

│
├── examples/                      # 真机可复跑示例 + 用户界面
	│   ├── agent_cli.py              # miniyu 终端界面（CLI 交互式对话）
	│   ├── miniyu_web.py             # miniyu Web 桌面客户端（Flask）
	│   ├── qq_send_message_demo.py    # 真实 QQ：点击+探针+OCR门 搜索并发送消息
│   ├── browser_doubao_demo.py     # 免登录豆包游客对话：真实鼠标聚焦+insertText+回车（无需登录可复跑）
│   └── browser_deepseek_demo.py   # CDP 驱动 DeepSeek 网页对话：发消息、读回复、截图（需登录）
│
├── docs/                          # 文档
│   ├── 接口文档.docx               # 接口文档
│   ├── 设计文档.docx               # 设计文档
│   ├── test_report.md             # 单元测试报告
│   ├── mcp_research.md            # MCP协议调研报告
│   └── adr/
│       ├── 0001-sort_directory-tool.md  # 排序工具决策记录
│       ├── 0002-index-files-tool.md     # 索引工具决策记录
│       ├── 0003-archive-directory-tool.md  # 归档工具决策记录
│       ├── 0004-manage-archive-tool.md  # 压缩/解压一体重塑记录
│       ├── 0005-skill-library-extension.md # 技能库扩展记录
│       ├── 0006-search-modes.md         # 搜索精确/模糊匹配增强记录
│       ├── 0007-path-naming-unification.md # 命名归位与隐藏别名决策
│       ├── 0008-app-send-message-click-probe.md # app_send_message 改点击+探针路线
│       ├── 0009-agent-screen-inspect-artifacts.md # Agent截图理解+过程产物生命周期
│       ├── 0010-read-image-local.md   # 本地图片直读 read_image（视觉双通道扩展）
│       └── 0011-programming-project-tools.md # 编程项目辅助：search_in_files + edit_file
│
├── CONTEXT.md                     # 术语表（文件/目录/路径命名约定）
	├── config.yaml                   # miniyu 配置文件（用户可直接编辑，不改代码）
	├── run_miniyu_cli.bat            # 一键启动终端界面（Windows）
	├── run_miniyu_web.bat            # 一键启动 Web 界面（Windows）
	├── main.py                        # Demo入口
├── README.md                      # 本文件
├── requirements.txt               # 依赖说明
├── 本组项目任务.md                  # 组任务说明
├── 设计任务书.pdf                   # 项目总任务书
└── 第4组项目进度跟踪.md             # 项目进度跟踪
```

---

# 5. 已实现工具列表（共59个）

| 工具名称 | 功能 | 类别 |
|----------|------|------|
| copy_file | 复制文件 | 文件操作 |
| move_path | 移动文件/目录 | 文件操作 |
| delete_file | 删除文件 | 文件操作 |
| rename_path | 重命名文件/目录 | 文件操作 |
| create_file | 创建空文件 | 文件操作 |
| create_directory | 创建目录 | 文件夹操作 |
| delete_directory | 删除目录 | 文件夹操作 |
| list_directory | 查看目录内容 | 文件夹操作 |
| sort_directory | 排序目录内容（带元信息） | 文件夹操作 |
| index_files | 递归建立文件元信息索引（可持久化） | 文件夹操作 |
| manage_archive | 打包/解压 zip（compress/extract） | 文件夹操作 |
| file_exists | 判断文件是否存在 | 文件查询 |
| get_file_size | 获取文件大小 | 文件查询 |
| get_path_metadata | 获取文件/目录完整元数据 | 文件查询 |
| get_directory_metadata | 获取目录统计信息 | 文件查询 |
| check_file_access | 检查文件读/写/执行权限 | 文件查询 |
| file_checksum | 计算文件哈希（md5/sha1/sha256） | 完整性 |
| compare_files | 比较两个文本文件差异 | 完整性 |
| read_text_file | 读取文本文件 | 文本文件 |
| write_text_file | 写入文本文件 | 文本文件 |
| batch_copy | 一次复制多个文件 | 批量操作 |
| batch_move | 一次移动多个文件 | 批量操作 |
| disk_usage | 查询磁盘空间使用情况 | 磁盘管理 |
| directory_size | 查询目录占用空间 | 磁盘管理 |
| find_empty_files | 查找空文件（0字节） | 磁盘整理 |
| find_empty_directories | 查找空目录 | 磁盘整理 |
| find_old_files | 查找长期未修改文件 | 磁盘整理 |
| find_large_files | 查找目录下的大文件（按大小降序，min_size_mb/limit 可调） | 磁盘整理 |
| search_files | 按文件名模式搜索（glob 风格，如 *.pdf / 关键词） | 磁盘整理 |
| search_in_files | 在文件内容中搜索关键词/正则（带行号与原文） | 编程开发 |
| edit_file | 局部编辑文本文件（查找原文片段精确替换，不整文件重写） | 编程开发 |
| get_image_metadata | 获取图片宽高/格式/色彩/EXIF | 图片处理（需Pillow） |
| rotate_image | 旋转图片 | 图片处理（需Pillow） |
| current_directory | 获取当前路径 | 系统工具 |
| run_command | 执行系统命令 | 系统工具 |
| list_windows | 列出所有可见窗口 | 应用操作 |
| find_window | 按标题/进程名查找窗口 | 应用操作 |
| activate_window | 激活窗口到前台（含前台锁定容错） | 应用操作 |
| send_hotkey | 发送快捷键组合 | 应用操作 |
| send_text | 向焦点输入文本（中文安全） | 应用操作 |
| take_screenshot | 截取当前屏幕 | 应用操作 |
| click_at | 在屏幕坐标左键单击（配合截图视觉定位） | 应用操作 |
| browser_launch | 启动并连接浏览器（Chrome/Edge/Chromium） | 浏览器控制 |
| browser_close | 关闭浏览器连接 | 浏览器控制 |
| browser_navigate | 导航到指定 URL | 浏览器控制 |
| browser_snapshot | 提取可交互元素索引清单 | 浏览器控制 |
| browser_click | 按快照索引/选择器点击元素 | 浏览器控制 |
| browser_type | 向元素输入文本（中文安全） | 浏览器控制 |
| browser_read_text | 读取页面/指定元素文本 | 浏览器控制 |
| browser_screenshot | 对当前页面截图 | 浏览器控制 |
| browser_wait | 等待元素/文本出现 | 浏览器控制 |
| list_processes | 列出当前运行的进程 | 系统管理 |
| get_process_info | 获取指定进程的详细信息 | 系统管理 |
| kill_process | 强制终止进程 | 系统管理 |
| terminate_process | 优雅终止进程 | 系统管理 |
| network_status | 获取网络接口状态 | 系统管理 |
| ping_host | 测试主机网络连通性 | 系统管理 |
| list_env_vars | 列出环境变量 | 系统管理 |
| get_env_var | 获取指定环境变量的值 | 系统管理 |

**命名约定（避免文件/目录混淆）**：
- `_file` 后缀（如 `delete_file`、`compare_files`）＝**仅操作文件**
- `_directory` 后缀（如 `create_directory`、`list_directory`）＝**操作目录**
- `_path` 后缀（如 `move_path`、`get_path_metadata`）＝**文件与目录皆可**

旧名（如 `move_file`、`create_folder`、`get_file_metadata`）作为**隐藏别名**保留：仍可被 `call` 调用（结果归一化为规范名），但不在 `list_tools()` 中显示。详见 `CONTEXT.md` 与 `docs/adr/0007-path-naming-unification.md`。

---

# 6. 已实现OS Skills（共24个）

| Skill | 功能 | 说明 |
|-------|------|------|
| system_info | 获取系统信息 | 系统/版本/CPU/Python |
| organize_downloads | 自动整理下载目录 | 按文档/图片/压缩包分类 |
| cleanup_temp | 清理临时文件 | Windows TEMP / Linux /tmp |
| search_file | 文件搜索 | 递归搜索，支持 substring/exact/wildcard/regex |
| find_large_files | 找出最大 N 个文件 | 递归按大小降序 |
| find_recent_files | 找最近修改的文件 | 按 days 时间窗过滤 |
| summarize_files | 目录统计 | 文件数/子目录/总大小/扩展名分布 |
| cleanup_by_type | 按扩展名批量清理 | 删除匹配扩展名文件 |
| batch_archive | 批量压缩/解压 | 一次处理多个目录或 zip |
| duplicate_finder | 按大小发现疑似重复 | 大小相同的文件分组 |
| rebuild_index | 一键重建并落盘索引 | 写 JSON 索引（与 index_files 同构） |
| query_index | 索引文件模糊查询 | 复用索引、不遍历磁盘，支持 substring/exact/wildcard/regex |
| trash_file | 移入回收站（可恢复） | 防永久删除，记录原路径 |
| restore_file | 从回收站恢复文件 | 恢复到原位置 |
| empty_trash | 清空回收站 | 需二次确认（confirm=True） |
| safe_delete | 安全删除（预览+确认） | 默认移入回收站而非永久删除 |
| deduplicate_files | 按内容哈希去重 | 移动到回收站，支持保留规则 |
| backup_file | 备份文件 | 生成 .bak 带时间戳 |
| restore_backup | 从备份恢复 | 恢复到指定/原名 |
| smart_organize | 智能整理（按类型/日期） | 识别→分类→移动→生成报告 |
| app_open | 打开指定应用 | 启动可执行文件 |
| app_send_message | 在应用内搜索并发送消息 | 组合窗口/键鼠/输入，端到端完成 |
| read_qq_chat | 读取 QQ 会话最近聊天（只读） | 搜索进入目标会话→OCR 核对会话标题→截图转写聊天区返回文本 |
| send_email | 发送邮件并自动回读核验 | SMTP 发信 + IMAP 回读发件箱『已发送』（可核验收件人收件箱『确实到达』），对外不可撤回、HIGH 确认门 |
| browser_search | HTTP 联网搜索 | 直接请求必应结果页→解析前几条{标题/链接/摘要}（不开浏览器） |
| browser_extract | 打开网页并提取内容 | 结构化读取正文/指定元素 |

---

# 7. 调用统计

ToolRegistry 内置调用统计功能：

```python
# 获取统计信息
stats = registry.get_stats()
# 返回：
# {
#   "total_calls": 10,
#   "by_tool": {"copy_file": 3, "move_path": 2, ...},
#   "success_rate": 90.0,
#   "most_used": [{"tool": "copy_file", "calls": 3}, ...]
# }

# 清空日志
registry.clear_logs()
```

---

# 8. 使用示例

## 8.1 直接使用

```python
from core.tool_registry import ToolRegistry

registry = ToolRegistry()

# 列出所有工具
print(registry.list_tools())

# 调用工具
result = registry.call("copy_file", {
    "src": "a.txt",
    "dest": "b.txt"
})
print(result)
# {"success": True, "tool": "copy_file", "result": "已复制：a.txt -> b.txt"}

# 排序目录内容（带元信息）
sorted_result = registry.call("sort_directory", {
    "path": "~/Downloads",
    "sort_by": "size",      # name / size / mtime，默认 name
    "reverse": True,        # 降序（默认升序）
})
print(sorted_result["result"])
# [{"name": "b_big.pdf", "type": "file", "size": 500, "mtime": 1721.0}, ...]
# 目录优先于文件；目录 size 为 None；次序为主键排序、相等时按 name 兜底

# 递归建立文件索引（可持久化）
idx = registry.call("index_files", {
    "path": "~/Downloads",
    "recursive": True,
    "persist": "./downloads_index.json",   # 保存为 JSON，跨调用复用
})
# 之后加载（不遍历文件系统）
idx2 = registry.call("index_files", {
    "path": "~/Downloads",
    "load": "./downloads_index.json",
})
# 每条目: {"path": <绝对路径>, "name", "type", "size", "mtime"}

# 压缩目录为 zip（未指定 dest 时同级生成 {目录名}_archive.zip）
arch = registry.call("manage_archive", {
    "action": "compress",
    "src_dir": "~/Downloads",
    "dest_zip": "./backup.zip",
})
print(arch["result"])   # 生成后的 zip 绝对路径

# 解压到默认目录 {zip名}_extracted（目标已存在则报错）
ext = registry.call("manage_archive", {
    "action": "extract",
    "dest_zip": "./backup.zip",
})
print(ext["result"])    # 解压后的目录绝对路径
```

## 8.2 通过统一接口

```python
from core.os_service_api import OSServiceAPI

api = OSServiceAPI()

# 调用工具
api.execute_tool("copy_file", {"src": "a.txt", "dest": "b.txt"})

# 调用技能
api.run_skill("system_info")

# 查看统计
api.get_tool_stats()
```

## 8.3 使用Mock

```python
from mock import MockOSServiceAPI

mock_api = MockOSServiceAPI()
result = mock_api.execute_tool("copy_file", {
    "src": "a.txt", "dest": "b.txt"
})
# {"success": True, "tool": "copy_file", "result": "[MOCK] 复制文件: a.txt -> b.txt"}
```

## 8.4 扩展技能示例

```python
from core.skill_library import SkillLibrary

skills = SkillLibrary()

# 找出最大的 5 个文件
skills.call("find_large_files", {"parent": "~/Downloads", "top": 5})

# 目录统计
skills.call("summarize_files", {"parent": "~/Downloads"})

# 一键重建并落盘索引，随后模糊查询（复用索引，不遍历磁盘）
idx = skills.call("rebuild_index", {"parent": "~/Downloads"})
skills.call("query_index", {"path": idx["result"]["index_path"], "keyword": "report"})
# 精确 / 通配符 / 正则匹配
skills.call("search_file", {"directory": ".", "keyword": "data.csv", "mode": "exact"})
skills.call("search_file", {"directory": ".", "keyword": "*.py", "mode": "wildcard"})
skills.call("search_file", {"directory": ".", "keyword": r"report\d\.log", "mode": "regex"})

# 批量压缩两个目录
skills.call("batch_archive", {"items": ["./a", "./b"], "action": "compress"})
```

## 8.5 应用操作示例

```python
from core.tool_registry import ToolRegistry

registry = ToolRegistry()

# 列出所有可见窗口
registry.call("list_windows", {})

# 按进程名查找窗口（如 QQ）
registry.call("find_window", {"process": "QQ"})

# 激活窗口到前台（内置前台锁定容错）
registry.call("activate_window", {"hwnd": 329390})

# 发送快捷键组合
registry.call("send_hotkey", {"keys": ["ctrl", "f"]})

# 输入中文文本（走剪贴板，中文安全）
registry.call("send_text", {"text": "你好"})

# 截图（保存到临时目录并返回绝对路径）
registry.call("take_screenshot", {})

# 在屏幕坐标左键单击（配合截图做视觉定位：截图→找到目标坐标→点它。
# 适合 QQ 这类自绘 UI，键盘回车常落到"查看资料卡"而非目标控件）
registry.call("click_at", {"x": 1157, "y": 1523})

# 组合技能：在 QQ 中搜索群并发送消息。
# 重写后的 app_send_message 用"点击 + 探针定位"，不走会被 QQ 判成资料卡的 Ctrl+F+Enter；
# 可通过 verify 回调注入"视觉 OCR 标题门"，命中目标会话才发送。
# 真机跑通示例见 examples/qq_send_message_demo.py。
from core.skill_library import SkillLibrary
skills = SkillLibrary()
skills.call("app_send_message", {
    "app_name": "QQ",
    "search_keyword": "我的手机",   # 演示目标默认发给自己（examples/qq_send_message_demo.py 默认值）
    "message": "你好",
})

# 组合技能：发送一封邮件（SMTP 发信 + 自动 IMAP 回读核验）。
# 发信账号/授权码从 config.yaml 的 email 段读取，绝不进参数；skill 内部发完会回读
# 发件箱『已发送』确认落库（配了 email.verify_inbox 再核验收件人收件箱『确实到达』），
# 回读未命中如实报 found=False，不把没验到当成功。真机示例见 examples/email_demo.py。
skills.call("send_email", {
    "to": "someone@qq.com",
    "subject": "来自小余人的测试信",
    "body": "你好呀，小余人",
    # "cc": "cc@qq.com",   # 可选抄送
    "verify": True,        # 默认 True：发送后自动回读核验
})
```

## 8.6 浏览器结构化控制示例

```python
from core.tool_registry import ToolRegistry
from core.skill_library import SkillLibrary

registry = ToolRegistry()

# 启动并连接浏览器（默认无头模式；首次调用才实例化）
registry.call("browser_launch", {})

# 打开网页
registry.call("browser_navigate", {"url": "https://www.bing.com/"})

# 提取可交互元素索引清单（Agent 的"眼睛"）
snap = registry.call("browser_snapshot", {})
print(snap["result"])
# [{"index": 0, "tag": "input", "role": "input", "type": "search", "text": ""}, ...]

# 按索引输入文本（中文安全，走 CDP Input.insertText）
registry.call("browser_type", {"text": "浏览器控制", "index": 0})

# 等待文本出现后读取正文
registry.call("browser_wait", {"text": "浏览器控制", "timeout": 10})
registry.call("browser_read_text", {})

# 组合技能：结构化搜索 / 内容提取
skills = SkillLibrary()
skills.call("browser_search", {"query": "文件管理系统", "engine": "bing"})
skills.call("browser_extract", {"url": "https://example.com", "selector": "article"})

# 关闭连接
registry.call("browser_close", {})
```

## 8.7 真机验证记录（非 mock 的真实软件自动化）

单元测试走 mock，验证的是接口契约；以下为**在真实桌面软件 / 真实网页上**跑通的能力，
可复跑脚本见 `examples/`：

| 验证项 | 结果 | 说明 |
|--------|------|------|
| 真实 QQ 全自动发消息 | ✅ | `examples/qq_send_message_demo.py`：探针定位搜索框→输入关键词→点选搜索结果（OCR 标题门确认命中"我的手机"才进入）→探针定位聊天输入框→Ctrl+A 覆盖→粘贴→回车。返回 `success=True` 并截图取证 |
| 聊天内容读取 + 上下文回复 | ✅ | 截图裁剪消息区→视觉桥转写成带发言人记录→据此生成回复再发送（群内按授权连发多条并互动） |
| 浏览器 DeepSeek 对话（CDP） | ✅ | `examples/browser_deepseek_demo.py`：attach 调试 Edge→textarea 定位→insertText 输入"你好"→Enter→读 AI 回复→截图（视觉桥确认文字进框、回复到达）。**需登录** |
| 浏览器豆包对话·免登录（CDP） | ✅ | `examples/browser_doubao_demo.py`：全新独立档案=豆包眼里的新设备→游客额度每次重置→contenteditable 输入条用真实鼠标点击聚焦→insertText 输入"你好"→Enter→读回复。**全程无需登录、可无人值守**（DOM+OCR 双取证） |

关键边界（真机踩坑所得，详见 `docs/adr/0008-*.md` 与进度跟踪）：

- **Chrome/Edge ≥136 禁止 `--remote-debugging-port` 作用于默认档案**（防本地偷 cookie）。
  要用 CDP 必须配独立 `--user-data-dir`，并在其中登录一次目标站点；
  默认档案的登录态无法被 CDP 接管。
- DeepSeek 网页版必须登录（无游客模式），且登录态为会话 cookie——**浏览器进程重启后通常需重登**。
- **免登录替代方案（demo 用）**：豆包网页版有游客模式，用"全新独立档案"启动即被视作新设备、游客额度重置，
  一条消息+读回复足够，`browser_doubao_demo.py` 可无人值守复跑，不受登录态拖累。
- 桌面 QQ 消息区不支持 Ctrl+A 全选复制整段对话；读取历史靠截图+视觉桥。
- 网页版 QQ 官方早已关停，无官方网页群聊客户端。

---

# 9. 返回格式规范

## 成功

```json
{
    "success": true,
    "tool": "copy_file",
    "result": "已复制：a.txt -> b.txt"
}
```

或

```json
{
    "success": true,
    "skill": "system_info",
    "result": {"system": "Windows", ...}
}
```

## 失败

```json
{
    "success": false,
    "tool": "copy_file",
    "error": "文件不存在"
}
```

### 错误码（error_code）约定

失败响应额外携带 `error_code`，便于上层（如第3组 ToolClient / 第5组审计）统一消费：

| error_code | 含义 | 场景 |
|---|---|---|
| `TOOL_NOT_FOUND` | 工具未注册 | 调用不存在的工具名（连带 `suggestion` 提示） |
| `TOOL_ERROR` | 工具执行异常 | 参数缺失、路径不存在、权限不足等 |
| `SKILL_NOT_FOUND` | 技能未注册 | run_skill 调用不存在的技能 |
| `SKILL_ERROR` | 技能执行异常 | 技能内部抛错 |

该字段与第3组 `ToolClient` 契约一致（`TOOL_NOT_FOUND`/`TOOL_ERROR`），保证跨组联调错误语义统一。

---

# 10. 运行测试

```bash
# 运行所有测试（共 494 个）
python -m pytest tests/ -v

# 运行单个测试文件
python -m pytest tests/test_tool_registry.py -v
python -m pytest tests/test_skills.py -v
python -m pytest tests/test_os_service_api.py -v
python -m pytest tests/test_mock.py -v
python -m pytest tests/test_app_tools.py -v
python -m pytest tests/test_agentic.py -v
python -m pytest tests/test_agent_config.py -v
python -m pytest tests/test_conversation.py -v
python -m pytest tests/test_llm_client.py -v
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_agent_skills.py -v
python -m pytest tests/test_email.py -v
python -m pytest tests/test_authz.py -v
python -m pytest tests/test_agent_read_web.py -v

> 💡 运行搜索相关测试（`TestSearchModes`）会实时列出每个模式实际命中的搜索结果，例如：
> ```
> [search] mode=wildcard keyword='*.pdf' -> 1 match: ['monthly_report.pdf']
> [query ] mode=exact    keyword='REPORT.TXT' -> 1 match: ['report.txt']
> ```

---

# 11. 与MCP协议的对应关系

本项目参考了 MCP（Model Context Protocol）协议设计接口。详见 [MCP调研报告](docs/mcp_research.md)。

| MCP概念 | 本组实现 |
|---------|---------|
| tools/list | ToolRegistry.list_tools() / OSServiceAPI.list_available_tools() |
| tools/call | ToolRegistry.call() / OSServiceAPI.execute_tool() |
| Tool定义（name, description, inputSchema） | 可参考MCP方式扩展工具元数据 |
| JSON-RPC 2.0 | 采用自定义 `{success, result, error}` 格式 |
| Transport（stdio/HTTP） | Python直接调用，不涉及网络传输 |

---

# 12. 里程碑记录与后续扩展

## 第3周里程碑（已完成）
- 完成 OS Skills 扩展与 **Agent 编排层**（ReAct 循环，离线脑 + 真实 LLM 双模式，安全确认门），并支持与第3组（AppAgent）的跨组接口联调
- 完善 Mock 与正式版的切换机制
- **2026-09-06 真实 LLM 真机联调通过**：阿里云百炼（DashScope compatible-mode，模型 qwen3.5-plus）驱动**多步文件系统工具调用**验证通过，证据见 `docs/evidence/agent_qwen_live.md`
- **2026-09-06 Agent 可调组合技能接线**：把 SkillLibrary 的组合技能（QQ 搜索+发送 `app_send_message`）以 OpenAI function 暴露给真实 LLM，Agent 走 `_execute_skill` 分发并默认强制「屏幕 OCR 核对目标会话」门 + HIGH 确认门——模型不再退化成激活窗口/输字的零散原语，避免发错会话
- **2026-09-06 Agent 截图理解 + 过程产物生命周期**：`qwen3.5-plus` 原生多模态（真机探测通过），开启 `supports_vision`；修掉"截图文件路径被当 base64"的坏图 bug，截图以真 base64 独立观测消息回传；新增 Agent 可调能力函数 **screen_inspect**（视觉模型直接看图 / 无视觉模型走项目内视觉桥 `core.vision_bridge`，读 config.yaml 的 `vision_bridge` 段——独立第二个视觉 API；不再依赖本机 ~/.claude 的外部 node 脚本，别人填 key 即用），GUI 成功/失败都自动补图；截图等过程产物单独存会话产物目录（可被下一技能复用），支持「清理截图/清理产物」与 `/reset` 联动清空，规范见 ADR 0009
- **2026-09-06 视觉桥可移植化（项目内 vision_bridge + config.yaml.example）**：看屏幕/OCR 的默认视觉桥迁进项目内 `core/vision_bridge.py`——`llm.supports_vision` 决定用谁看图/OCR：主对话有视觉(true)直接用主模型读图（不必第二个 key）；主对话纯文本(false)走 config.yaml 顶层**独立 `vision_bridge` 段**（可与主对话不同 key/厂商，即"填两个 API"）。删掉 demo/文档里本机绝对路径（`C:\Users\34808\...\qwen-vision`），新增 `config.yaml.example` 模板（两种填法：单 key 有视觉主模型 / 双 key + 视觉桥）。外部 node 桥仅作显式 `vision_js`/`AGENT_VISION_JS` 的旧通道保留。全量测试 332→**343 全绿**
- **2026-09-06 授权档位（基础 / 高级 / 全自动）**：把「高危操作确认门」从二值开关升级成三档可切换授权 `agent.authorization`——base=基础授权(全部高危需确认，现状)；advanced=高级授权(仅永久删除文件需确认，run_command·kill_process·QQ 发消息·发邮件自动放行)；full=全自动(从不确认，含删除)。`core/safety.py` 新增纯函数 `should_confirm(level,name,risk)` 与 `resolve_authz_level`（旧 `confirm_high_risk=false` ⇔ full，配置不回归）；Agent 运行时确认门（`_execute_tool`/`_execute_skill`）改读档位；Web 顶栏新增授权档位下拉（`/switch-authz` 持久化 config.yaml + 热改运行中 agent 不丢会话，切「全自动」先弹一次浏览器确认把关），CLI 加 `/authz` 命令；引擎级 fail-safe（call_safely/run_skill_safely）保持按 HIGH 不变，QQ 发送的屏幕 OCR 核对门在高级/全自动下仍生效（自动放行≠盲发）。新增 `tests/test_authz.py` 21 例 → **370→391 全绿**（15 个测试文件）
- **2026-09-06 邮件全链路（自验证技能 send_email）**：新增 `core/email_client.py`（纯 stdlib：`smtplib`/`imaplib`/`email`，零新增依赖）+ Agent 白名单组合技能 **send_email**——SMTP 发信后自动 IMAP 回读发件箱『已发送』核验已落库；config.yaml 的 email 段配 `verify_inbox`（收件侧邮箱 IMAP）再轮询收件人收件箱核验『确实到达』（双端闭环），回读未命中如实报 found=False。授权码与 `llm.api_key` 同级敏感：只进 config/env、不入库（config.yaml.email 段入库留空、本地填回后不要再 commit）。**工具 57 不变、技能 24→25**；send_email 走 HIGH 确认门（对外发送、不可撤回）。新增 `examples/email_demo.py`（直驱技能 / `--agent` 真实 LLM 双模式 + 收尾独立 IMAP 核验）。单测全绿 343→**368**（新增 `tests/test_email.py` 25 例：fail-closed、协议层、技能编排、Agent 分发、Mock 对等）。**QQ 真机实跑**（2026-09-06）：主号发小号 `你好呀，小余人`，SMTP 接受、**到达核验命中（收件箱真收到）**；实跑暴露并修复 3 个协议层坑——QQ 投递改写 Message-ID、中文主题存 RFC2047 编码（→ 头解码后按主题兜底匹配）、发件箱名 `Sent Messages` 带空格需按 RFC3501 加引号；另发现 **QQ 授权码 SMTP 不在发件箱留副本** → 发件箱未命中≠没发，双端闭环下以收件箱到达核验为铁证。单测 **368→370**（test_email 25→27，+SELECT 引号、+RFC2047 主题命中 2 条回归）。证据 `docs/evidence/email_live_qq.md`

- **2026-09-06 QQ『先读后回』只读技能 + 联网白名单（给模型"能读、能查"的上下文）**：针对真机反馈"让它找 QQ 群、看聊天回话，它说 send 技能只能发不能读"——根因是**模型能"想"的范围只有上下文里出现的函数**，能力没暴露＝对它不存在。修复：① 新只读技能 **read_qq_chat**（搜索进入目标会话→OCR 核对会话标题（`make_ocr_verify`，读错群会失败关闭）→截图裁聊天区→视觉桥转写最近聊天返回文本，只读不外发）；② Agent 白名单由 2 扩到 **5**（app_send_message / **read_qq_chat** / send_email / **browser_search** / **browser_extract**），SYSTEM_PROMPT 新守则：QQ 找群看聊天＝先 `read_qq_chat` 再 `app_send_message`，联网查资料＝`browser_search`→`browser_extract` 并标注来源、不得编造；③ 技能 25→**26**（`core/vision_bridge` 缺视觉源时明确报错引导，不假装读到）。全量 **391→404 全绿**（16 个测试文件：新增 `tests/test_agent_read_web.py` 11 例 + `test_agent_skills` +2，mock 技能库/白名单对等）
- **2026-09-06 联网搜索改 HTTP 直连（修真机"思考 3 分钟仍超时"）**：真机让 miniyu 搜 CSGO 赛果 → 反复"搜索超时"约 3 分钟。定位根因＝旧 `browser_search` 走无头浏览器"打字+合成回车"，但 **CDP 合成回车在必应/百度都不触发提交**（实测词已打进框、URL 停在首页），内部 wait_for 每次必 10s 超时、模型重试数次堆到分钟级；百度另有"安全验证"人机墙。修复＝新增 `core/web_search.py`（纯标准库、零依赖）**HTTP 直接请求必应结果页再解析 `b_algo` → {标题/链接/摘要}**，与云端联网搜索同构；基址**直连 `cn.bing.com`**（`www` 会 302 跳 cn、CN 网络下二次握手偶发被重置 WinError 10054）+ **3 次连接级重试**兜底，实测 **~0.6s** 返回 5 条结果。schema/engine 收敛为仅 `bing`（百度验证墙未接入），FAQ/技能表同步。全量 **404→415 全绿**（17 个测试文件：新增 `tests/test_web_http.py` 11 例，离网不联网）
- **2026-09-07 FailoverClient 自动降级（主 API → 本地 Ollama → 确定性脑）**：`core/llm_client.py` 新增 FailoverClient——主 API 超时/鉴权失败/网络不通自动切本地 Ollama（5 分钟定时重试、恢复自动升级回主 API），本地也没有则落确定性脑；`config.yaml` 新增 `llm.fallback` 段（不配则行为不变，向后兼容）。新增 9 个测试，全量 **424 全绿**
- **2026-09-07 模型切换三连修 + 可搜索模型下拉框（Web UI）**：真机暴露三个根因逐一修复——① 热切换失效：`/switch-model` 只查 `_agent.llm.model` 而 FailoverClient 没有该属性（模型名在 `primary.model`），切换被静默跳过；② 切完模型对话报"Failed to fetch"：实为上游 **HTTP 400**（历史残留 `tool_calls`/`tool` 消息，不支持 function calling 的模型直接拒收）→ `core/agent.py` 新增 `_sanitize_tool_messages()` 换模型前自动把工具调用消息转普通文本，跨模型无缝接力历史；③ 本地模型"切而不换"：只改了 fallback 模型名但主 API 可用时不走备用 → FailoverClient 新增 **`force_local_mode()`**（选本地模型即跳过云端直接本地作答，选在线模型即关闭），`status`/`Agent.model_name` 同步透出。UI 重写为**可搜索 Combobox**：输入即过滤、☁️在线/🖥️本地分组、Enter/Esc 键盘操作、点选即切；修 mousedown 事件绑定 + 加 CORS/OPTIONS 支持。内嵌浏览器实测：3 个在线模型 + 本地 7B 模型往返切换对话全通
- **2026-09-07 工具可见性修复 + 文本协议自适应（换模型后 Agent"看不见 57 工具"）**：根因＝ReAct 只在 `provider=openai_compatible` 时把 tools 发给 LLM，配置 fallback 后变 FailoverClient（provider=failover）→ **工具列表根本没发给模型**，磁盘/QQ 全失效。修复：① failover 分支正常透传 tools；② 对**不支持 tools 参数的模型**（如 deepseek-r1-distill 实测 HTTP 400 "The tool call is not supported."）自动降级为**文本协议**——工具 JSON Schema 注入 system prompt，模型用 `<tool_call>{"name":…,"arguments":…}</tool_call>` 标记输出，客户端正则解析回正常 ReAct 循环（对标 Qwen-Agent/早期 LangChain 对无 function calling 模型的通用解），chat 与 chat_stream 双路径适配；③ `_request_messages` 不再无条件清理历史工具消息（保留多步"调用→结果"上下文），不支持时由 client 收到 400 按需降级；④ 主/备模型热切换后 `reset_runtime_flags()` 重新探测新模型工具能力。真机实测：qwen3.6-flash 磁盘/QQ 指令全通，deepseek-r1-distill 不再 400、用文本协议调 disk_usage 拿到真实数据。新增 `tests/test_tool_protocol.py` 19 个测试，全量 **443 全绿**
- **2026-09-07 接入百炼服务端联网搜索（enable_search）：不自研搜索，用阿里云云端工具**：官方文档核实 + 真实 API 探针五场景实测——百炼 Chat Completions 下 `enable_search: true` 云端透明执行、**与本地 62 个函数工具同请求混用无冲突**（模型直接用注入的搜索资料作答）；对照组证实不开服务端搜索时模型确实会调本地 browser_search → 生效期间对模型**隐藏本地 browser_search**（每步动态评估，API 拒绝后自动还原）。`config.yaml` 新增 `llm.bailian_tools.web_search` 开关，仅 base_url 指向百炼（aliyuncs.com）时生效；`server_tools` 注入 + 400 自适应停注 + Failover 降级/force_local 返回 False 让 agent 还原本地工具。新增 `tests/test_bailian_tools.py` 16 例，**459 全绿**；端到端真机（e2e_bailian_search.py）：qwen3.6-flash 问"杭州今天天气"→ 服务端搜索生效、browser_search 已隐藏、直接给出实时天气数据（未调任何工具）
- **2026-09-07 联网搜索总开关（DeepSeek 式：一个开关管所有联网能力）**：实测模型会自己判断要不要搜（知识题不触发、时效题才搜），但 enable_search 开着时**每次请求注入约 3K token 固定搜索指令**（知识题 prompt_tokens 24→3088）且按次计费 → 做成用户可控总开关。配置重构：删除 `llm.bailian_tools` 段、新增**顶层 `web_search.enabled`**（默认 true）三态语义——开+百炼=服务端搜索注入 + browser_search 隐藏；开+其他=本地搜索工具全可见模型自决；关=完全离线、不注入、本地搜索工具全隐藏。Agent 层 `_visible_tools()` 三分支裁剪 + `set_web_search_enabled()` 运行时切换；Web 顶栏「🌐 联网: 开/关」按钮 + `/toggle-web-search`。新增 3 例扩到 19 例，**462 全绿**；端到端真机（e2e_web_search_switch.py）：开 62/63 → 关 61/63 → 切回开还原；关态问天气模型如实答"离线模式无法获取实时数据"、未调隐藏工具、未编造
- **2026-09-07 本地模型纯对话模式（tool_free）+ Web SSE 流式 + DeepSeek 式思考展示**：实测本地 7B 纯 CPU 写 800 token 要 132s、带 63 工具 schema 光 prompt eval 就 33~150s → 用户决策本地模型只做聊天不接工具。`tool_free` 静默忽略 tools（本地端点 localhost 自动 true，config fallback 段显式标注）；Agent 轻量对话分支 `_chat_mode()`（自述离线限制、建议切在线模型办工具类任务）；`chat_stream` 捕获 `reasoning_content`/`reasoning` 思考通道。Web 改 **SSE 事件队列**：`/chat` 立即返 task_id → 后台 `run_stream` 逐 chunk 入队 → `/task/<id>/events` 推送（reasoning/token/tool_call/confirm/done/error + 15s 心跳 + confirm 应答，done/error 自动清理）；前端思考实时滚动 + 计时、完成折叠"已深度思考（用时 Xs）"、正文逐字渐进。新增 `tests/test_tool_free_sse.py` 21 例，**483 全绿**；端到端真机（e2e_local_chat_stream.py 14/14）：force_local 切本地写故事首 token 9~20s（原 33~150s）、思考 1400+ 字 + 正文正常；场景 B 主 API 挂掉自动降级 degraded 本地回复；场景 C Web SSE 434 事件按序到达、任务无泄漏
- **2026-09-07 本地模型乱码修复（UTF-8 增量解码）+「停止生成」打断（DeepSeek 式）**：问题一＝三个本地模型回复全变 `æ°è½æº` 乱码——根因（实测）Ollama 流式响应头不带 charset，requests `iter_content(decode_unicode=True)` 按 ISO-8859-1 误解码 UTF-8 中文（**不是模型乱码，是 Python 客户端解码层出错**）→ `chat_stream` 改按字节读流 + `codecs.getincrementaldecoder("utf-8")` 增量解码（天然容忍多字节跨网络块）；问题二＝本地小模型死循环/超长输出无法打断 → 三层：`run_stream(stop_check=...)` 回调（轮次/流式 chunk 间隙检查，保留已流出文本 + stop chunk 收尾）＋ Web `/task/<id>/stop` 端点（幂等）＋ 前端红色「⏹ 停止」按钮。新增 `tests/test_stream_stop_encoding.py` 11 例，**494 全绿**；端到端真机（e2e_stop_encoding_live.py）：1.5B/4B/7B 三模型中文全正常、打断终态 <0.5s、保留部分文本
- **2026-09-08 四连发：本地图片直读 + 编程项目辅助 + KaTeX 公式渲染 + 深浅主题**：① **read_image 直接读本地图片**（给路径即分析，不必先打开屏幕靠截屏）——主模型有原生视觉（`llm.supports_vision: true`）直接看图，无视觉自动走 `core/vision_bridge` 转文字描述，文件缺失/两源都没配好时明确报错不瞎编；模型可见函数集 = **59 工具 + 5 白名单技能 + screen_inspect/read_image 视觉双通道（共 66）**；② **编程项目工作流**：新增 `search_in_files`（按内容搜关键词/正则、带行号原文）+ `edit_file`（原文片段精确替换，不整文件重写），SYSTEM_PROMPT 新守则第 11 条「先侦察、后动手」（list_directory→search_files→search_in_files→read_text_file→edit_file/write_text_file→run_command 实跑验证），小改不整重写、报错喂回搜索定位根因；③ **Web 前端 KaTeX 公式渲染**：`$...$`/`$$...$$` → 正常数学排版（先保护 LaTeX 片段防 marked 吞反斜杠 → marked 转 HTML → DOMPurify 消毒 → renderMathInElement），KaTeX/marked/DOMPurify **本地打包** `examples/static/vendor/`（离线可用，CDN 兜底回退）；④ **顶栏主题按钮**（会话数徽章左侧 ☀️/🌙）：深/浅色 CSS 变量切换 + localStorage 记忆 + 首帧防闪烁。全量 **512 全绿**；内嵌浏览器实测：主题切换持久化、行内+块级公式渲染（katex-display）、Markdown 加粗/代码正常、vendor 本地加载零 404

## 第4周计划
- 工具签名验证（可选）
- 权限控制与白名单
- 整体系统集成
- 最终报告与PPT

---

# 13. 技术特点

- **跨平台** — 兼容 Windows / Linux / macOS，使用 `pathlib` 统一路径处理，内置编码安全打印
- **模块化** — 工具、技能、接口相互独立，新增工具只需注册无需改调用逻辑
- **可扩展** — 支持动态注册/注销工具和技能
- **可测试** — 494个单元测试覆盖核心功能，支持Mock测试和接口联调测试
- **可统计** — 内置调用次数、成功率、Top5排名等统计功能

# 14. 跨平台兼容说明

本项目在以下系统上测试通过：

| 系统 | 状态 | 说明 |
|------|------|------|
| Windows 11 | ✅ 通过 | 494个测试全部通过，Demo正常运行 |
| Ubuntu/Linux | ✅ 兼容 | 使用 `pathlib` / `shutil` 等跨平台库，无需修改 |
| macOS | ✅ 预期兼容 | 内部测试未进行，理论兼容 |

> **真实 LLM 真机联调（2026-09-06）**：阿里云百炼（DashScope compatible-mode，模型 qwen3.5-plus）已完成真机验证，真实 LLM 驱动多步**文件系统工具调用**跑通，详见 `docs/evidence/agent_qwen_live.md`。

跨平台设计要点：
- **路径处理**：全部使用 `pathlib.Path`，自动适配系统分隔符
- **编码安全**：`safe_print()` 函数防止 Windows GBK 终端崩溃
- **系统检测**：通过 `platform.system()` 自动识别并适配
- **默认路径**：下载目录、临时目录自动适配各系统

Linux 使用要点（Ubuntu 22.04+，详见 `setup_linux.sh` / `run_miniyu_*.sh`）：
- **启动**：`bash setup_linux.sh`（一键装依赖+生成配置）→ `bash run_miniyu_web.sh`（或命令行 `run_miniyu_cli.sh`）。
- **桌面自动化需 X11（Xorg）会话**：`app_controller` 的 Linux 实现基于 `xdotool`/`wmctrl`/`xclip`/`gnome-screenshot`，这些是 X11 工具。Ubuntu 默认 **Wayland** 会话下它们不可用——本机检测到 Wayland 会提示切「Ubuntu on Xorg」。仅做文件管理 / Web 聊天不受影响。
- **语音输入**：浏览器原生 Web Speech API，仅 **Chrome / Edge** 支持（Edge 推荐、国内可用）。
- **发给他人**：他的机器 `cp config.yaml.example config.yaml` 填自己的 key 即可完整使用；你的 API key 与本地模型不随代码分发（`config.yaml` 含敏感字段、不入库，靠 `.gitignore` + skip-worktree 保护）。
