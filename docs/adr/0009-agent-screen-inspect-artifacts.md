# Agent 截图理解（视觉通道）+ 过程产物生命周期

日期：2026-09-06。背景来自 2026-09-05 的 QQ 技能（ADR 0008，技能内部用
qwen-vision 视觉桥做发送前 OCR 标题门）与同学建议（"AI 调 skill 的产出文件最好单独建个文件夹，
方便下一个 skill 调用，流程化"）。

## 问题：模型"看得见"被三处没接通

1. **配置默认关掉视觉**：`config.yaml` 的 `llm.supports_vision: false`，而真实模型
   `qwen3.5-plus`（阿里云百炼 compatible-mode）**原生接受 base64 图片**（2026-09-06 真机探测：
   一张真实 PNG 内联 `image_url` 后模型回 `OK`；此前一次 400「image format illegal」是发送了一张
   过小的 1×1 假 PNG 所致）。视觉通道被关 ≠ 模型无视觉。
2. **坏图 bug**：`Agent._capture_and_add_image` 把 `take_screenshot` 返回的**文件路径字符串**
   当 base64 直接拼 `data:image/png;base64,C:\...\xxx.png`，模型拿到的是坏图。
   即便开了视觉也看不到。
3. **没有"主动看一眼"的入口**：自动截图只在执行 `_GUI_TOOLS` 后触发、且把图堆到最早那条
   user 消息上；模型自己在"不确定/出错/复杂界面"时**不能主动截屏理解**，只能回复
   "不会处理/做不了"。

## 决策

- **视觉通道两路自适应**：
  - 视觉模型（`llm.supports_vision=True`）→ 截图以**真实 base64** 作为独立的"截图观测"消息
    （`conversation.add_observation_image`）加入对话，模型下一轮直接看图；
  - 无视觉模型（如部分 DeepSeek）→ 调 **项目内视觉桥** `core/vision_bridge`（读 config.yaml
    顶层 `vision_bridge` 段配的独立视觉 API）把图转成文字描述返回；找不到视觉源则**明确报错**，
    不做假描述。（早期实现沿 ADR 0008 用外部 node 脚本 qwen-vision，2026-09-06 迁为项目内、
    可独立第二个 key；外部桥保留为显式 `vision_js`/`AGENT_VISION_JS` 旧通道，详编排设计决策 10。）
- **`screen_inspect` 作为 Agent 自带能力函数暴露给模型**（不是 SkillLibrary 技能，不改变
  57 工具 / 24 技能计数）：模型在规则 7（SYSTEM_PROMPT）指导下，对拿不准的状态主动
  `screen_inspect(question)`。**它是只读操作、无确认门**。
- **修坏图 bug**：`_capture_and_add_image`/`_execute_screen_inspect` 先落盘再读文件 → 真 base64；
  截图观测以独立消息追加在对应 tool 结果**之后**（满足 OpenAI 兼容 API 对 tool 消息与
  assistant tool_calls 的配对顺序）。
- **GUI 成功与失败都截图**：执行 `_GUI_TOOLS` 里某工具后（成功用于回看、失败用于诊断），
  在视觉模型下补一张观测图。
- **过程产物单独建目录（回应同学"分离 + 可接力"）**：
  - 位置：`<artifacts_root>/<会话ID>/shots/...`。`artifacts_root` 顺序 =
    环境变量 `AGENT_ARTIFACTS_DIR` > `config.memory.artifacts_dir` > 系统临时目录
    `%TEMP%\miniyu_artifacts`（不污染项目、不带入上交包；但同任务内路径稳定，可被下一技能复用）。
  - 截图等过程工件与技能的正式返回值、对话正文、项目代码**分离**；回传给模型的只有
    "文字结论 + 产物路径"（视觉模型才额外看原图）。
- **何时提醒用户发指令清除**：
  - 本轮产生过产物 → 最终回复末尾附一句提醒（含数量、目录、屏幕隐私提示）；
  - 触发词：说「清理截图 / 清除截图 / 清理产物 / 清除产物 / 清空截图 / 清理产出 /
    删除过程产物 / 清理过程产物」即清空**当前会话**产物目录（不清聊天记录、不动用户文件）；
  - `/reset`（含 Web「重置」）**联动清空**当前会话产物。

## 局限与边界

- 原生视觉是否可用以 `llm.supports_vision` 为开关：若经 Web 切到不支持视觉的模型，需把该值
  改 `false`（届时 `screen_inspect` 自动走视觉桥）；目前真机默认模型 qwen3.5-plus 已开 true。
- `screen_inspect` 截的是整屏（可能含用户隐私/其他窗口），产物只留在本机会话目录，提醒文字
  明示"可能带屏幕内容，可清理"。
- 自动截图只在视觉模型 + `_GUI_TOOLS` 下触发；文本模型不要自动图（模型看不到只会涨 token）。
