# 编程项目辅助：search_in_files（按内容搜索）+ edit_file（精确局部替换）

日期：2026-09-08。背景来自用户需求："既然能处理图片里的问题，那也要让它像人一样帮忙处理
**编程项目**的问题——查 bug、改代码、理解项目结构，请完善好它，让它成为真正的 AI 助手。"

## 问题：现有工具集改代码"只能整文件重写"

1. 已有 `search_files` 只能**按文件名**搜（glob 风格），查 bug 时最常遇到的场景是
   "报错信息/函数名/关键词出现在哪个文件哪一行"——按文件名搜不到。
2. 已有 `write_text_file` 是**整文件覆盖写**：模型要改一行代码也得先读全文再重写整个文件，
   读不全/记错都会把用户文件搞坏；缺少"找到一段原文、精确替换"的局部编辑能力。
3. SYSTEM_PROMPT 没有对"查 bug/改代码"的标准路径指导，模型容易凭猜测整文件重写或乱试。

## 决策

- **新增 `search_in_files(root, keyword, include_exts=None, max_results=20, timeout=10)`**：
  在目录内**文本文件内容**中搜索关键词/正则（`re.IGNORECASE`），返回文件路径 + 行号 + 原文
  行，支持扩展名过滤与超时保护。只读工具（READ_ONLY，无确认门）。
- **新增 `edit_file(path, old_text, new_text, occurrence=1)`**：读文件 → 找 `old_text`
  原文（逐字一致，含缩进空格）→ 替换为 `new_text`（默认第一处，`occurrence` 可指定第 N 处
  或 `'all'` 全换）→ 保留原编码写回 → 返回编辑摘要（第几处/行号/字节变化）。风险 MEDIUM
  （写操作走确认门）。
- **SYSTEM_PROMPT 新增守则第 11 条「先侦察、后动手」**：
  list_directory → search_files（按文件名）→ search_in_files（按内容/报错信息）→
  read_text_file（大文件 `max_bytes` 限长分段读）→ 理解清楚后才改：小改动
  `edit_file`、新建/大段重写才 `write_text_file` → `run_command` 实跑验证
  （`python xxx.py` / `pytest` / `git status`）→ 报错喂回 search_in_files 定位根因 →
  中文总结改了哪些文件、为什么。禁止凭猜测整文件重写。
- 工具总数 57 → **59**；`safety.py` TOOL_META 登记两个新工具的风险分级与类别「编程开发」。

## 影响

- 模型对"这个脚本为什么报错 / 帮我修好某函数 / 这个项目结构是啥样"有可循的标准动作序列，
  且改动最小化（局部替换而非整文件重写），破坏用户文件的概率显著下降。
- Mock 版（`mock/mock_tools.py`）同步注册两把工具保持对等。
- 测试：`tests/test_tool_registry.py` 新增 TestProgrammingTools（内容搜索命中/正则/
  扩展名过滤/超时、edit_file 第一处/all/找不到原文报错）；4 个测试文件的工具数断言
  57→59 同步更新。

## 相关

- `core/tool_registry.py`（search_in_files / edit_file 实现）
- `core/safety.py`（TOOL_META 风险登记）
- `core/agent.py`（SYSTEM_PROMPT 守则 11）
