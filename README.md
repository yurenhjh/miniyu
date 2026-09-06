# Group4 - miniyu 桌面 AI 助手（Tool Registry + OS Skills + Agent 编排层）

## 完整桌面 AI 助手（LLM 驱动，function-calling 调用 57 个系统工具 + 26 个技能）

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

### 可移植性

整个项目使用**相对路径**，无硬编码绝对路径。别人拿到项目后：
1. `pip install -r requirements.txt`
2. 编辑 `config.yaml`（可选）
3. `python examples/agent_cli.py` 或双击 `run_miniyu_cli.bat`

即能在任何 Windows 机器上运行。

---

# 2. 项目简介

本项目为课程设计 **第4组：miniyu 桌面 AI 助手**（LLM 驱动，function-calling 调用 57 个系统工具 + 26 个技能），底层以"工具注册 + OS Skills（Tool Registry + OS Skills）"作为统一系统能力接口。

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
- 覆盖全部57个工具和24个技能

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
| **Web 桌面客户端** | `examples/miniyu_web.py` | Flask + 深色主题，高危操作弹模态框确认；顶栏可切换授权档位（基础 / 高级 / 全自动） |

### 架构

```
用户输入 "帮我整理桌面"
    ↓
 Conversation（对话记忆，滑动窗口 20 条）
    ↓
 Agent（ReAct 循环）
    ├── LLMClient（抽象基类）
    │   ├── DeterministicBrain（离线脑，规则匹配，零依赖，默认）
    │   └── OpenAICompatibleClient（真实 LLM，兼容 GPT/DeepSeek/豆包/Ollama 等）
    ↓
 OSServiceAPI（57 个工具 + 26 个技能）
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
| ReAct 循环 | `core/agent.py` → `Agent` | 安全确认门、最大步数保护、GUI 截图回传 |
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
│   ├── tool_registry.py           # 工具注册表（正式版，57 工具，含 click_at + 系统管理）
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
│   ├── mock_tools.py              # Mock版工具注册表（57个工具）
│   └── mock_skills.py             # Mock版技能库（26个技能）
│
├── tests/                         # 单元测试（共 415 个，全部通过）
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
│       └── 0009-agent-screen-inspect-artifacts.md # Agent截图理解+过程产物生命周期
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

# 5. 已实现工具列表（共57个）

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
# 运行所有测试（共 415 个）
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
- **可测试** — 415个单元测试覆盖核心功能，支持Mock测试和接口联调测试
- **可统计** — 内置调用次数、成功率、Top5排名等统计功能

# 14. 跨平台兼容说明

本项目在以下系统上测试通过：

| 系统 | 状态 | 说明 |
|------|------|------|
| Windows 11 | ✅ 通过 | 415个测试全部通过，Demo正常运行 |
| Ubuntu/Linux | ✅ 兼容 | 使用 `pathlib` / `shutil` 等跨平台库，无需修改 |
| macOS | ✅ 预期兼容 | 内部测试未进行，理论兼容 |

> **真实 LLM 真机联调（2026-09-06）**：阿里云百炼（DashScope compatible-mode，模型 qwen3.5-plus）已完成真机验证，真实 LLM 驱动多步**文件系统工具调用**跑通，详见 `docs/evidence/agent_qwen_live.md`。

跨平台设计要点：
- **路径处理**：全部使用 `pathlib.Path`，自动适配系统分隔符
- **编码安全**：`safe_print()` 函数防止 Windows GBK 终端崩溃
- **系统检测**：通过 `platform.system()` 自动识别并适配
- **默认路径**：下载目录、临时目录自动适配各系统
