# 真机验证证据：真实 LLM（阿里云百炼 qwen）↔ miniyu 文件系统

- 日期：2026-09-06
- 后端：阿里云百炼 DashScope（OpenAI 兼容 `compatible-mode`）
- 模型：`qwen3.5-plus`（该 Key 可见 249 个模型；用户指定 Qwen3.5-Plus 免费档）
- base_url：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 链路：真实 LLM function-calling → `Agent`（system prompt + tool_calls 解析 + 逐 id 回填）→ `OSServiceAPI/ToolRegistry` → Windows 文件系统
- 驱动：`examples/live_llm_check.py`（密钥经环境变量注入，**未写入仓库任何文件**；`qwen-vision/config.json` 仅运行时读取）
- 结果：非流式与流式各跑一遍，**均 5/5 独立核验通过**，stderr 全程为空。

## 1. 做了什么

在 Windows 临时隔离目录 `%TEMP%\miniyu_live_*` 内，让 qwen3.5-plus 用中文指令完成一段多步文件系统对话：

1. 创建文件夹「报告」；
2. 在「报告」内写文件 `摘要.txt`（内容为固定中文串）；
3. 列出「报告」目录内容；
4. 查看磁盘剩余空间。

跑完后**不由 LLM 自证**，而由驱动独立 `iterdir()` + 读回文件逐字比对 + `shutil.disk_usage` 自查磁盘。

## 2. 真实工具调用序列（两次运行一致）

模型原生返回 tool_calls（非 JSON 文本兜底），按序执行：

```
create_directory(path=…\miniyu_live_xxxx\报告)        → 目录创建成功
write_text_file(path=…\报告\摘要.txt, content=…)      → 写入完成
list_directory(path=…\miniyu_live_xxxx\报告)           → [{"name": "摘要.txt", "type": "file"}]
disk_usage(path=…\miniyu_live_xxxx)                    → total/used/free/percent_used
```

最终模型给出自然语言总结（中文，复述每一步），随后结束（`finish_reason=stop`）。

## 3. 文件系统独立核验（驱动自查，不信模型措辞）

| 核验点 | 非流式 | 流式 |
|---|---|---|
| 「报告」目录真实存在 | ✅ | ✅ |
| `摘要.txt` 真实存在 | ✅ | ✅ |
| `摘要.txt` 内容与要求逐字一致 | ✅ | ✅ |
| 「报告」内 listing == `['摘要.txt']` | ✅ | ✅ |
| 磁盘 total/free 可读且 > 0 | ✅ | ✅ |

实测磁盘：total ≈ 287.66 GB，free ≈ 121.78 GB（与工具返回值一致，差异为驱动自查瞬间的并发写入）。

## 4. 流式收尾修复的验证点

`--stream` 模式输出 `[流式] 收到 1 个最终 stop 终止块`，即：

- 纯文本回复流自然结束后，`run_stream` 在**一轮内**补发 `finish_reason="stop"` 并收尾（修复前会因缺终止块反复重调 LLM 直到 `max_steps`）；
- 全程仅调用一轮，未出现二次重入。

## 5. 诚实备注

- 本轮是**真实联网**调用：耗时非流式 33.8s、流式 18.6s（含多轮工具往返），stderr 无网络/鉴权/额度/解析告警。
- 本 Key 模型名可见但**不代表免计费**：实际计费/免费额度以百炼控制台为准，本证据不承诺费用为 0。
- LLM 输出天然有随机性：本次断言以「工具被真实调用 + 文件系统实测一致」为准，而非模型措辞。
- 多工具**并行** tool_calls 的逐条 id 回填属静态回归（`tests/test_agent.py::test_multi_tool_call_id_pairing`），真机本轮为顺序调用，未覆盖并行分支；相关缺陷已由单测锁定。
- 临时目录运行后已自动删除；本机 `qwen-vision/config.json` 中的 API Key 未写入仓库、日志或本文档。

## 6. Web 端 HTTP 端到端（补充验证，2026-09-06）

用户确认后，key 已写入 `config.yaml`（`llm.provider=openai_compatible` / `base_url` / `api_key` / `model=qwen3.5-plus`），因此 Web 端 **零环境变量**即可走真机：

- 服务：`examples/miniyu_web.py`（Flask，`127.0.0.1:5000`）。
- 流程：`GET /status`（agent 未建时返回 deterministic）→ `POST /chat` 发多步文件系统指令（隔离临时目录）→ 轮询 `GET /task/<id>` 至 `done`。
- 结果：qwen3.5-plus 完成「创建 报告 目录 → 写 摘要.txt → 列目录 → 查磁盘」；任务 `done` 后 `GET /status` 返回 `{provider: openai_compatible, model: qwen3.5-plus, tools: 57}`。
- 文件系统独立核验 **4/4**：目录存在 / 文件存在 / 内容与要求逐字一致 / listing=`['摘要.txt']`。
- 测试后已停止服务，并清理测试生成的临时会话与脚本。

> 注意：`config.yaml` 内为明文 key，仓库非 git、纯本地，可接受；若日后要分享/上传代码，记得先移除。

## 7. Web 端模型切换 + 切换后连通实测（2026-09-06）

百炼是**聚合多模型平台**（一次接入，可切换其网关提供的不同厂商模型），据此给 Web 加了顶栏**模型下拉框**切换，切换时**先实测连通、成功才生效**：

- `GET /api/models`：调 DashScope 兼容 `GET /models`，返回**该 key 可用模型**，过滤 embedding/rerank/文生图等非对话模型。真机实测返回 **192 个对话模型**（含 qwen3.5-plus、以及 MiniMax / ZHIPU-GLM / Qwen 系列等聚合模型）；拉取失败则回退常用候选并提示。
- `POST /switch-model`：用目标模型发一条最小 chat（`连通性测试`）**实测**——
  - **连通** → 把 `llm.model` 文本级写回 `config.yaml`（保留中文注释与其余字段）+ **热切换**运行中 agent（只改 `llm.model`，不重建，当前会话与上下文不丢）；前端弹绿色 ✅“切换成功 … 可正常使用”。
  - **不连通** → **不改动**当前模型与配置，前端弹红色 ❌“切换失败：… 请换个模型试试”（含 HTTP 状态与后端原始原因）。
- 离线 / 未配置 API：下拉禁用并提示“先接入 API”，`/switch-model` 也拒绝并给出接入指引。

真机三分支实测（结果全部符合预期）：

| 分支 | 结果 |
|---|---|
| 切到平台聚合的 `MiniMax-M2.1` | ✅ 成功 → `/status model=MiniMax-M2.1`，并**用该模型真机对话**回复“在的” |
| 切回 `qwen3.5-plus` | ✅ 成功，config 已恢复 `qwen3.5-plus` |
| 切不存在的 `no-such-model-xyz-2026` | ❌ `HTTP 404 … does not exist`，提示换模型，**当前模型未变** |
| 离线（`AGENT_LLM_PROVIDER=deterministic`） | `/api/models` online=false、`/switch-model` ok=false，均提示接入 API |

- 配套单测：`tests/test_model_switch.py` 12 个（config 写回只改 model 行 / 非对话模型过滤 / 缺凭据不发网络）。测试总数 297 → **309 全绿**。

## 8. Agent 可调组合技能接线：QQ 搜索+发送技能成为“模型可调用函数”（2026-09-06）

背景：真实 LLM 会话里让 miniyu“在 QQ 里找某群并发送消息”时，它只激活了 QQ 窗口、朝当前聚焦的会话打字并警告可能发错——因为 Agent 只把 ToolRegistry **57 个底层工具**暴露给模型，昨天做的 `app_send_message`（SkillLibrary 技能）模型看不见、也经 `_execute_one`（只调 `execute_tool`）调不到。

本轮把这条接线补齐（由 `tests/test_agent_skills.py` 13 个单测锁定，**测试 309 → 322 全绿**）：

- `core/skill_library.py`：Agent 白名单 `_AGENT_SKILL_SCHEMAS` + `openai_skill_names/is_agent_skill/list_openai_tools`；`app_send_message` 以 OpenAI function 暴露（schema 只给 `app_name/search_keyword/message/verify_ocr`，**不暴露** Python 闭包 `verify`）。技能加 `verify_ocr/vision_js`，`verify_ocr=True` 时由 `make_ocr_verify` 内部构造“屏幕 OCR 门”（裁剪聊天标题横带 → qwen-vision 视觉桥 OCR，标题必须含关键词才放行），找不到视觉桥**明确报错、绝不盲发第一行**。
- `core/os_service_api.py` + `core/agent.py`：`run`/`run_stream` 给模型的 tools = `57 底层工具 + 白名单技能`；`_execute_one` 拆 `_execute_tool`/`_execute_skill`（技能走 `run_skill`、HIGH 确认门照旧；发送类技能模型没显式关 OCR 时**默认强制 `verify_ocr=True`**）；system prompt 加“QQ 发消息必须用 app_send_message、绝不拆零散步骤”守则。
- mock 层接口对齐（`MockSkillLibrary`/`MockOSServiceAPI`），换机演示不会因缺新方法崩。

**诚实备注**：本轮只验证”接线/门逻辑”（单测 + 真机之外的分发冒烟），**没有**对真实”一中兄弟会之大压抑时代”群自动发消息——外发不可撤回，需用户本人在 miniyu 里下达并确认。真机复测方式见 `miniyu使用说明.md` §2：登录桌面 QQ（经典布局）后自然语言下达，观察它走 OCR 核对弹窗，确认无误才发。

## 9. Agent 截图理解：原生视觉真机探测 + 产物生命周期（2026-09-06）

背景：真实 LLM 对话里模型对”复杂/出错场景”回”不会处理”，排查发现三处没接通——①`config.yaml` 把
`supports_vision` 关成 `false`；②`Agent._capture_and_add_image` 把 `take_screenshot` 返回的
**文件路径字符串当 base64** 拼 data URL（坏图）；③模型没有”不确定时主动截图看一眼”的可调入口。
规范见 `docs/adr/0009-agent-screen-inspect-artifacts.md`。

**原生视觉真机探测（本小节为真实联网调用）**：

| 探测 | 载荷 | 结果 |
|---|---|---|
| 一张真实生成的 PNG（315 字节，PIL 画 160×100 纯色）内联 `image_url` | content=[image_url+text] | ✅ 模型回 `OK` —— qwen3.5-plus **原生接受 base64 图片** |
| 一张 1×1 极小 PNG 内联 | 同上 | ❌ HTTP 400 `image format illegal`（图片本身非法，非模型不支持视觉） |

结论：`config.yaml` 的 `llm.supports_vision: true` 是**可用的**（此前 400 是探测载荷问题，
已用真实 PNG 复核通过）。

**代码落地（322 → 332 全绿，新增 tests/test_agent_vision.py 10 个）**：
- 修坏图：`_capture_and_add_image`/`_execute_screen_inspect` 先落盘→读文件→真 base64；
  截图以独立”观测消息”（`conversation.add_observation_image`）在对应 tool 结果**之后**加入对话，
  满足 tool_call 配对顺序。
- 新 Agent 能力函数 `screen_inspect(question)`（只读）：`supports_vision=True` → 截图内联给模型看；
  `False`（如部分 DeepSeek）→ 走 qwen-vision 视觉桥转文字，找不到桥明确报错。
- 过程产物单独存会话产物目录 `<artifacts_root>/<会话ID>/shots/`（`AGENT_ARTIFACTS_DIR` >
  `config.memory.artifacts_dir` > `%TEMP%\miniyu_artifacts`），与对话记录/项目代码分离、可被下一技能复用；
  用户说「清理截图/清理产物」即清空当前会话产物，`/reset` 联动清空，本轮产生过产物时回复末尾附提醒。
- **诚实备注**：本轮**未**把 `screen_inspect` 接到一次真实 GUI 出错现场端到端走通（需用户在有问题的真机
  App 会话里让 miniyu 主动截屏复现）；已由单测覆盖双通道分发与产物清理，真机端到端复测方式：
  真机对话下指令做一步 GUI 操作后让模型”不确定就自己截图看看再继续”，观察它调用 screen_inspect。

## 10. 视觉桥可移植化（2026-09-06 同日，现行默认行为）

上面 §8/§9 写于当时，其"无视觉走外部 node 桥 / OCR 门交 qwen-vision"到当天已进一步收口，
**现行默认**如下（`core/vision_bridge.py` + `config.yaml`，单测 `332 → 343 全绿`）：

- 看图/OCR 用谁只由 `llm.supports_vision` 决定：
  - `true`（主对话有视觉，如本机 qwen3.5-plus）→ 截图/OCR **直接由主对话模型**处理（不必第二个 key）；
  - `false`（主对话纯文本）→ 走 config.yaml 顶层 `vision_bridge` 段（**独立第二个视觉 API**，可与
    主对话不同 key/厂商）。本机现为 `true`，`vision_bridge` 段已备好，属"切纯文本主对话即生效"的备胎。
- `core/vision_bridge.describe_image(image, question)` 统一做上面选源并请求，`make_ocr_verify`（QQ 发送前
  OCR 门）与 `screen_inspect` 文字通道都经它；两源都没有/失败 → `VisionBridgeError` 给配置指引。
- 外部 node 脚本 qwen-vision 不再是默认依赖：demo 里 `C:\Users\34808\...` 绝对路径已删；保留为显式
  `vision_js` / `AGENT_VISION_JS` 的旧通道。`config.yaml.example` 模板给了两种填法（单 key 有视觉主模型 /
  双 key + 视觉桥）。
